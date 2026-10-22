"""The parts of the release gate that `scripts/verify.py` runs (R6 T7 split).

Why: verify.py had grown to 1300 lines. It stays the CLI; this package holds the steps, the
state files, the matrix glue and the report.
"""
