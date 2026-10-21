"""Seed or remove the test-only rows of the browser check (R2 phase 7).

Why: `scripts/browser_qa.py` presses real controls. It does that on rows made here, marked
`qa:`, never on a real person (see `saley.qa.seed` for why a seeded write cannot reach the
sheet). browser_qa runs seed, the checks, then teardown in `finally`; this command lets a
person do the same by hand.

Run:  uv run python scripts/qa_seed.py seed        (dev database only)
      uv run python scripts/qa_seed.py teardown
"""

from __future__ import annotations

import argparse
import sys

from saley.core.db import session_scope
from saley.qa import seed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("seed", "teardown"))
    args = parser.parse_args(argv)
    try:
        with session_scope() as session:
            if args.action == "seed":
                print(f"seeded: {seed.seed(session).summary()}")
            else:
                removed = seed.teardown(session)
                print("removed: " + ", ".join(f"{n} {k}" for k, n in removed.items()))
    except seed.SeedRefused as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
