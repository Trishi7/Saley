"""Cell parsing for the GTM sheet. Pure functions, no I/O.

Why: the sheet mixes real dates and text like `02.09.2026` (docs/10). The reader asks Google
for UNFORMATTED values with SERIAL_NUMBER dates, so a real date arrives as a number and a text
date as a string. Text dates are read day-first (India, D38) and flagged for data quality.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

# Moved to core.text in R7 (the bot may not import sales); the sync code still imports them here.
from saley.core.text import clean_text, norm_name  # noqa: F401

SHEETS_EPOCH = date(1899, 12, 30)
# Columns are 32-bit integers. A phone number in a money cell must not crash the sync.
MAX_INT = 2_000_000_000
MIN_YEAR, MAX_YEAR = 2000, 2100

TEXT_DATE = "text_date"
# Words people type in a date cell that mean "no date yet". Not a data problem.
_NO_DATE_WORDS = frozenset({"not available", "n/a", "na", "tbd", "tba", "-", "—", "none"})
BAD_DATE = "bad_date"

_FALSE_WORDS = frozenset(
    {"", "false", "no", "n", "0", "-", "—", "na", "n/a", "none", "nil", "not sent"}
)
_TRUE_WORDS = frozenset({"true", "yes", "y", "1", "done", "sent", "x", "✓", "✔"})
# Text that says a step did NOT happen yet, whatever else it says.
_NOT_YET = re.compile(r"^(not|no|un|pending|tbd|to do|todo|planned|later|n\.?a\.?)\b")
_TEXT_DATE_FORMATS = (
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%y",
    "%d/%m/%y",
    "%Y-%m-%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d-%b-%Y",
    "%d %b, %Y",
)


def is_error_cell(value: object) -> bool:
    """A formula error such as #REF!, #N/A or #VALUE! is never data."""
    text = clean_text(value) or ""
    return text.startswith("#") and text.rstrip("!?").upper().lstrip("#").replace("/", "").isalpha()


def is_truthy(value: object) -> bool:
    """Checkbox or marker cell → bool.

    Names like 'VR' or 'Sid' and words like 'Sent' count as set. Text that starts with
    'not', 'no', 'pending', 'TBD', 'to do' or 'planned' does not.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    text = (clean_text(value) or "").casefold()
    if is_error_cell(text):
        return False
    return text not in _FALSE_WORDS and not _NOT_YET.match(text)


def tri_state(value: object) -> bool | None:
    """Yes / No / unknown for cells like 'Registered?' that may say 'not available'."""
    if isinstance(value, bool):
        return value
    text = (clean_text(value) or "").casefold()
    if text in _TRUE_WORDS:
        return True
    if text in {"false", "no", "n", "not started"}:
        return False
    return None


def parse_date(value: object) -> tuple[date | None, str | None]:
    """Return (date, problem). problem is None, 'text_date' (parsed, flagged) or 'bad_date'."""
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return None, BAD_DATE
    if isinstance(value, int | float):
        try:
            parsed = SHEETS_EPOCH + timedelta(days=int(value))
        except (OverflowError, ValueError):  # inf, nan
            return None, BAD_DATE
        return (parsed, None) if MIN_YEAR <= parsed.year <= MAX_YEAR else (None, BAD_DATE)
    text = clean_text(value)
    if text is None or text.casefold() in _NO_DATE_WORDS:
        return None, None
    for fmt in _TEXT_DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if MIN_YEAR <= parsed.year <= MAX_YEAR:
            return parsed, TEXT_DATE
    return None, BAD_DATE


def parse_int(value: object) -> int | None:
    """'12', 12.0, '1,200' → int. Anything else, or a number too big for the DB, → None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        try:
            number = int(value)
        except (OverflowError, ValueError):  # inf, nan
            return None
    else:
        match = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
        if not match:
            return None
        number = int(float(match.group(0).replace(",", "")))
    return number if abs(number) <= MAX_INT else None


def parse_pct(value: object) -> int | None:
    """0.3 → 30 (Sheets stores percent cells as fractions). '30%', '30.5%', 30 → 30 or 31."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, int | float):
        number = float(value) * 100 if 0 <= value <= 1 else float(value)
    else:
        text = str(value).strip()
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return None
        number = float(match.group(0))
        if "%" not in text and 0 <= number < 1:
            number *= 100
    return round(number) if 0 <= number <= 100 else None


def _amount(number: str, suffix: str) -> int | None:
    scale = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix.lower(), 1)
    amount = int(float(number.replace(",", "")) * scale)
    return amount if amount <= MAX_INT else None


def parse_money_band(value: object) -> tuple[int | None, int | None]:
    """'USD 30–50k' → (30000, 50000). '$1.5M - $2M' → (1500000, 2000000). Unknown → (None, None)."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        amount = parse_int(value)
        return amount, amount
    text = clean_text(value)
    if not text:
        return None, None
    number = r"(\d[\d,]*(?:\.\d+)?)\s*([kmb])?\b"
    money = r"(?:usd|inr|eur|gbp|[$₹€£])?\s*"
    band = re.search(money + number + r"\s*(?:-|–|—|to)\s*" + money + number, text, re.I)
    if band:
        low_n, low_s, high_n, high_s = band.groups()
        suffix = high_s or low_s or ""
        low, high = _amount(low_n, low_s or suffix), _amount(high_n, suffix)
        if low is None or high is None or low > high:
            return None, None
        return low, high
    single = re.search(money + number, text, re.I)
    if single:
        amount = _amount(single.group(1), single.group(2) or "")
        return amount, amount
    return None, None


def deal_value(value: object) -> int | None:
    """One USD value for a deal-size cell (D42): a band counts its midpoint, not its upper end.

    'USD 30–50k' → 40000. An exact value counts as given. Unknown → None, never 0.
    """
    low, high = parse_money_band(value)
    if low is None or high is None:
        return None
    return (low + high) // 2


def is_band(value: object) -> bool:
    """True when the cell is a range ('USD 30–50k'), so its value is a midpoint, not exact."""
    low, high = parse_money_band(value)
    return low is not None and high is not None and low != high


def parse_prob(value: object) -> int | None:
    """Closure Prob: '>50%' → 50, '<50%' → 49, '<=50%' → 50, '<1%' → 1 (never 0), '0%' → 0."""
    pct = parse_pct(value)
    if pct is None:
        return None
    text = (clean_text(value) or "").lstrip()
    if text.startswith("<") and not text.startswith("<="):
        return max(pct - 1, 1) if pct > 0 else 0
    return pct
