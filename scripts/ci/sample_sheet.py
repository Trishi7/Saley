"""Write the test sheet as JSON for the CI compose checks (D249): `saley sync --raw-file` and
`saley sheet verify --raw-file` read it. CI has no sheet credentials.

The rows are the same ones the tests sync (tests/conftest.py `default_sheet`), dated today in
IST, so the compose stack shows real numbers and the restore check compares real numbers.

    uv run python scripts/ci/sample_sheet.py <out.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: sample_sheet.py <out.json>", file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT))
    from tests.conftest import default_sheet

    from saley.core.clock import today_ist

    raw = default_sheet(today_ist())
    out = Path(argv[0])
    out.write_text(json.dumps(raw), encoding="utf-8")
    print(f"sample sheet: {len(raw)} tabs, {sum(len(r) for r in raw.values())} rows -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
