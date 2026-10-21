"""The sheet writer: one port, a fake for tests, and the real gspread writer (R2, D23, D45).

Why: Saley is the only writer of the GTM sheet (D23), and only the worker holds the write
credential. So the one place that builds a write client is `build_write_sheet()`, and it
refuses in any process that is not the worker. The web never imports this module
(tests/test_writer.py::test_web_process_has_no_write_credential checks the import graph).

`SheetPort` is what the outbox needs and nothing more. `FakeSheet` keeps cells in memory the
way Google returns them (dates as serial numbers, checkboxes as booleans), so every write
test runs without a network. `GspreadSheet` is never called in tests.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, Protocol

import gspread

from saley.core.clock import tz
from saley.core.egress import GuardedHTTPClient
from saley.core.settings import WORKER_PROCESS, current_process, get_settings
from saley.sales.columns import col_letter
from saley.sales.sheet import HEADER_SCAN_ROWS, SheetNotCopy

# The only write scope in the code base. Reading uses the .readonly scope (sheet.py).
WRITE_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

DATE_ROLES = frozenset({"first_contact_date", "li_connected_date", "meeting_date"})
SHEETS_EPOCH = date(1899, 12, 30)
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FORMULA_STARTS = ("=", "+", "-", "@")
NOTE_PREFIX = "Saley · "
NOTE_KEEP = 10  # D47: the last 10 Saley lines stay in the note


class WriteCredentialRefused(RuntimeError):
    """Something other than the worker asked for the sheet write credential (D23, D46)."""


class RowOccupied(RuntimeError):
    """The row below the last named row already holds text; an append would overwrite it (M2)."""


class FormulaRefused(ValueError):
    """A text that starts with = + - or @ would run as a formula under USER_ENTERED (H1)."""


def refuse_formula(value: str) -> None:
    """Last line of defence in the worker: no such text ever leaves for the sheet."""
    if value.lstrip().startswith(FORMULA_STARTS):
        raise FormulaRefused("the value starts with a formula sign")


def as_literal(value: str) -> str:
    """Restore only: an old cell like "-" or "+91…" goes back as plain text, never a formula.

    Under USER_ENTERED a leading apostrophe makes Sheets store the rest as text and drop the
    apostrophe (re-review MEDIUM: undo of such a cell must work, not end as undo_failed).
    """
    return "'" + value if value.lstrip().startswith(FORMULA_STARTS) else value


class SheetPort(Protocol):
    """What the outbox needs from a sheet. Rows are 1-based, columns are 0-based."""

    def read_header(self, tab: str, rows: int = HEADER_SCAN_ROWS) -> list[list[Any]]: ...

    def read_cell(self, tab: str, row: int, col: int) -> Any: ...

    def write_cell(self, tab: str, row: int, col: int, value: str) -> None: ...

    def read_note(self, tab: str, row: int, col: int) -> str: ...

    def write_note(self, tab: str, row: int, col: int, text: str) -> None: ...

    def find_row(self, tab: str, col: int, value: str, after: int = 0) -> int | None: ...

    def write_row(self, tab: str, row: int, values: list[str]) -> None: ...

    def read_column(self, tab: str, col: int) -> list[Any]: ...

    def read_row(self, tab: str, row: int) -> list[Any]: ...

    def append_row(self, tab: str, key_col: int, values: list[str]) -> int: ...

    def delete_row(self, tab: str, row: int) -> None: ...

    def tab_exists(self, title: str) -> bool: ...

    def add_tab(self, title: str, rows: int = 50, cols: int = 10) -> None: ...

    def delete_tab(self, title: str) -> None: ...


# ------------------------------------------------------------------ values


def serial_to_iso(serial: float) -> str:
    return (SHEETS_EPOCH + timedelta(days=int(serial))).isoformat()


def iso_to_serial(iso: str) -> int:
    return (date.fromisoformat(iso) - SHEETS_EPOCH).days


def norm_value(role: str, value: Any) -> str:
    """One text form for a cell value, so 'what we wrote' and 'what we read back' compare.

    Dates come back as serial numbers and checkboxes as booleans; both map to the text we
    send ('2026-10-19', 'TRUE'). Anything else is trimmed text.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        if role in DATE_ROLES:
            return serial_to_iso(value)
        return str(int(value)) if float(value).is_integer() else str(value)
    text = str(value).strip()
    if text.upper() in ("TRUE", "FALSE"):
        return text.upper()
    return text


def native_value(value: str) -> Any:
    """What Google stores after USER_ENTERED: ISO dates and checkboxes become typed cells."""
    text = value.strip()
    if text.upper() in ("TRUE", "FALSE"):
        return text.upper() == "TRUE"
    if ISO_DATE.match(text):
        try:
            return iso_to_serial(text)
        except ValueError:
            return value
    if re.fullmatch(r"-?\d{1,15}", text):
        return int(text)
    return value


# ------------------------------------------------------------------ Name note (D47)


def shown_value(value: str) -> str:
    """'2026-10-19' → '19 Oct 2026' for the note; other text as it is."""
    if ISO_DATE.match(value):
        try:
            day = date.fromisoformat(value)
        except ValueError:
            return value
        return f"{day.day} {day:%b %Y}"
    return value


def write_line(
    column: str, value: str, user: str, surface: str, when: datetime, decision_id: int
) -> str:
    local = when.astimezone(tz())
    stamp = f"{local:%a} {local.day} {local:%b %H:%M} IST"
    shown = shown_value(value)
    return f"{NOTE_PREFIX}{column} = {shown} · {user} · {surface} · {stamp} · d{decision_id}"


def row_added_line(user: str, surface: str, when: datetime, decision_id: int) -> str:
    """The signature on the Name cell of a row Saley added (docs/10, D76)."""
    local = when.astimezone(tz())
    stamp = f"{local:%a} {local.day} {local:%b %H:%M} IST"
    return f"{NOTE_PREFIX}row added · {user} · {surface} · {stamp} · d{decision_id}"


def undo_line(original_id: int, user: str, surface: str, when: datetime) -> str:
    local = when.astimezone(tz())
    return f"{NOTE_PREFIX}undone d{original_id} · {user} · {surface} · {local:%H:%M} IST"


def append_note(existing: str | None, line: str, keep: int = NOTE_KEEP) -> str:
    """Add one Saley line at the end. Keep the newest `keep` Saley lines and all human text."""
    lines = (existing or "").splitlines()
    saley_at = [i for i, text in enumerate(lines) if text.startswith(NOTE_PREFIX)]
    drop = set(saley_at[: max(0, len(saley_at) + 1 - keep)])
    kept = [text for i, text in enumerate(lines) if i not in drop]
    return "\n".join([*kept, line])


# ------------------------------------------------------------------ fake sheet (tests, dev)


class FakeSheet:
    """In-memory sheet. Tabs are lists of rows; row 1 is index 0."""

    def __init__(self, tabs: dict[str, list[list[Any]]] | None = None) -> None:
        self.tabs: dict[str, list[list[Any]]] = {
            title: [list(row) for row in rows] for title, rows in (tabs or {}).items()
        }
        self.notes: dict[tuple[str, int, int], str] = {}
        self.calls: list[tuple[str, ...]] = []

    def raw(self) -> dict[str, list[list[Any]]]:
        """The tabs the way `fetch_raw` returns them, ready for `build_tabs`."""
        return {title: [list(row) for row in rows] for title, rows in self.tabs.items()}

    def _tab(self, tab: str) -> list[list[Any]]:
        if tab not in self.tabs:
            raise KeyError(f"no tab {tab!r}")
        return self.tabs[tab]

    def read_header(self, tab: str, rows: int = HEADER_SCAN_ROWS) -> list[list[Any]]:
        self.calls.append(("read_header", tab))
        return [list(row) for row in self._tab(tab)[:rows]]

    def read_cell(self, tab: str, row: int, col: int) -> Any:
        self.calls.append(("read_cell", tab, str(row), str(col)))
        data = self._tab(tab)
        if row < 1 or row > len(data) or col >= len(data[row - 1]):
            return None
        return data[row - 1][col]

    def write_cell(self, tab: str, row: int, col: int, value: str) -> None:
        refuse_formula(value)
        if value.startswith("'"):
            value = value[1:]  # USER_ENTERED: the apostrophe only marks the rest as text
        self.calls.append(("write_cell", tab, str(row), str(col)))
        data = self._tab(tab)
        while len(data) < row:
            data.append([])
        cells = data[row - 1]
        cells.extend([""] * (col + 1 - len(cells)))
        cells[col] = native_value(value) if value != "" else ""

    def read_note(self, tab: str, row: int, col: int) -> str:
        return self.notes.get((tab, row, col), "")

    def write_note(self, tab: str, row: int, col: int, text: str) -> None:
        self.calls.append(("write_note", tab, str(row), str(col)))
        self.notes[(tab, row, col)] = text

    def find_row(self, tab: str, col: int, value: str, after: int = 0) -> int | None:
        wanted = value.strip().casefold()
        for number, cells in enumerate(self._tab(tab), start=1):
            if number > after and col < len(cells) and str(cells[col]).strip().casefold() == wanted:
                return number
        return None

    def write_row(self, tab: str, row: int, values: list[str]) -> None:
        for value in values:
            refuse_formula(value)
        self.calls.append(("write_row", tab, str(row)))
        for offset, value in enumerate(values):
            self.write_cell(tab, row, offset, value)

    def read_column(self, tab: str, col: int) -> list[Any]:
        self.calls.append(("read_column", tab, str(col)))
        return [cells[col] if col < len(cells) else "" for cells in self._tab(tab)]

    def read_row(self, tab: str, row: int) -> list[Any]:
        self.calls.append(("read_row", tab, str(row)))
        data = self._tab(tab)
        return list(data[row - 1]) if 1 <= row <= len(data) else []

    def append_row(self, tab: str, key_col: int, values: list[str]) -> int:
        """Write below the last row that has a key. Returns the 1-based row number.

        M2: the target row is read first. A row with text in any column (a note in another
        column, a half-filled line) is refused: the write sends blanks for every header column.
        """
        keys = self.read_column(tab, key_col)
        last = max((n for n, cell in enumerate(keys, 1) if str(cell).strip()), default=0)
        if any(norm_value("", cell) for cell in self.read_row(tab, last + 1)):
            raise RowOccupied(f"row {last + 1} below the last person already has text in it")
        self.write_row(tab, last + 1, values)
        return last + 1

    def delete_row(self, tab: str, row: int) -> None:
        self.calls.append(("delete_row", tab, str(row)))
        data = self._tab(tab)
        if not 1 <= row <= len(data):
            raise IndexError(f"no row {row} in {tab!r}")
        del data[row - 1]
        # Notes sit on cells: those of the deleted row go, the rows below move up one.
        self.notes = {
            (t, r - (r > row and t == tab), c): text
            for (t, r, c), text in self.notes.items()
            if not (t == tab and r == row)
        }

    def tab_exists(self, title: str) -> bool:
        return title in self.tabs

    def add_tab(self, title: str, rows: int = 50, cols: int = 10) -> None:
        if title in self.tabs:
            raise ValueError(f"tab {title!r} exists")
        self.tabs[title] = [[""] * cols for _ in range(rows)]

    def delete_tab(self, title: str) -> None:
        self.tabs.pop(title)


# ------------------------------------------------------------------ gspread (worker only)


class GspreadSheet:
    """The real sheet. Built only by `build_write_sheet()`; tests never call it.

    Deleting a tab is allowed for the scratch tab only (SHEET_TEST_TAB): a real tab can never
    be deleted by Saley.
    """

    def __init__(self, spreadsheet: Any, test_tab: str) -> None:
        self.spreadsheet = spreadsheet
        self.test_tab = test_tab

    def _ws(self, tab: str) -> Any:
        return self.spreadsheet.worksheet(tab)

    @staticmethod
    def _a1(row: int, col: int) -> str:
        return f"{col_letter(col)}{row}"

    def read_header(self, tab: str, rows: int = HEADER_SCAN_ROWS) -> list[list[Any]]:
        return self._ws(tab).get(
            f"A1:ZZ{rows}", value_render_option=gspread.utils.ValueRenderOption.unformatted
        )

    def read_cell(self, tab: str, row: int, col: int) -> Any:
        values = self._ws(tab).get(
            self._a1(row, col), value_render_option=gspread.utils.ValueRenderOption.unformatted
        )
        return values[0][0] if values and values[0] else None

    def write_cell(self, tab: str, row: int, col: int, value: str) -> None:
        refuse_formula(value)
        self._ws(tab).update(
            [[value]],
            self._a1(row, col),
            value_input_option=gspread.utils.ValueInputOption.user_entered,
        )

    def read_note(self, tab: str, row: int, col: int) -> str:
        notes = self._ws(tab).get_notes(grid_range=self._a1(row, col))
        return notes[0][0] if notes and notes[0] else ""

    def write_note(self, tab: str, row: int, col: int, text: str) -> None:
        self._ws(tab).update_note(self._a1(row, col), text)

    def find_row(self, tab: str, col: int, value: str, after: int = 0) -> int | None:
        wanted = value.strip().casefold()
        column = self._ws(tab).col_values(
            col + 1, value_render_option=gspread.utils.ValueRenderOption.unformatted
        )
        for number, cell in enumerate(column, start=1):
            if number > after and str(cell).strip().casefold() == wanted:
                return number
        return None

    def write_row(self, tab: str, row: int, values: list[str]) -> None:
        """One call for a whole row (the Google write quota is 60 a minute)."""
        for value in values:
            refuse_formula(value)
        self._ws(tab).update(
            [values],
            f"{self._a1(row, 0)}:{self._a1(row, max(len(values) - 1, 0))}",
            value_input_option=gspread.utils.ValueInputOption.user_entered,
        )

    def read_column(self, tab: str, col: int) -> list[Any]:
        return self._ws(tab).col_values(
            col + 1, value_render_option=gspread.utils.ValueRenderOption.unformatted
        )

    def read_row(self, tab: str, row: int) -> list[Any]:
        values = self._ws(tab).get(
            f"A{row}:ZZ{row}", value_render_option=gspread.utils.ValueRenderOption.unformatted
        )
        return list(values[0]) if values else []

    def append_row(self, tab: str, key_col: int, values: list[str]) -> int:
        """Write below the last row that has a key, never into a gap. One update call."""
        for value in values:
            refuse_formula(value)  # before any call to Google
        keys = self.read_column(tab, key_col)
        last = max((n for n, cell in enumerate(keys, 1) if str(cell).strip()), default=0)
        row = last + 1
        sheet = self._ws(tab)
        if row > sheet.row_count:
            sheet.add_rows(row - sheet.row_count)
        # M2: the write sends a blank for every header column; never over text that is there.
        if any(norm_value("", cell) for cell in self.read_row(tab, row)):
            raise RowOccupied(f"row {row} below the last person already has text in it")
        self.write_row(tab, row, values)
        return row

    def delete_row(self, tab: str, row: int) -> None:
        self._ws(tab).delete_rows(row)

    def tab_exists(self, title: str) -> bool:
        return any(ws.title == title for ws in self.spreadsheet.worksheets())

    def add_tab(self, title: str, rows: int = 50, cols: int = 10) -> None:
        if title != self.test_tab:
            raise WriteCredentialRefused("Saley adds only the scratch tab")
        self.spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)

    def delete_tab(self, title: str) -> None:
        if title != self.test_tab:
            raise WriteCredentialRefused("Saley deletes only the scratch tab")
        self.spreadsheet.del_worksheet(self._ws(title))


def _write_client() -> gspread.Client:
    return gspread.service_account(
        filename=get_settings().google_service_account_file,
        scopes=[WRITE_SCOPE],
        http_client=GuardedHTTPClient,
    )


def build_write_sheet(client_factory: Callable[[], Any] | None = None) -> GspreadSheet:
    """The only way to get a write client. Worker process only (D23, D46).

    R2 also refuses a sheet that is not the copy (proposed D53): writes to the live GTM sheet
    wait for a decision.
    """
    if current_process() != WORKER_PROCESS:
        raise WriteCredentialRefused("The sheet write credential is built only in the worker.")
    settings = get_settings()
    settings.require("worker")
    if not settings.gtm_sheet_is_copy:
        raise SheetNotCopy("GTM_SHEET_IS_COPY is false. R2 writes only the sheet copy.")
    client = (client_factory or _write_client)()
    return GspreadSheet(client.open_by_key(settings.gtm_sheet_id), settings.sheet_test_tab)
