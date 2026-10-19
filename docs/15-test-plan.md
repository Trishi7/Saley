# 15 · Test plan

Acceptance per ticket. Each prompt adds its rows. The tester agent runs them.

| Ticket | Test | How | Pass |
| --- | --- | --- | --- |
| P0 | health | `uv run saley health` | prints ok, db reachable |
| P0 | discord loop | `saley discord send --test "P0 ok"` then `read --test --last 1` | the message is read back |
| P0 | env contract | pytest tests/test_env_example.py | pass |
| P0 | egress | pytest tests/test_egress.py | linkedin.com blocked, allowlist host passes, rate limit holds |
| R1 | migrations | `uv run alembic upgrade head`, then `downgrade base`, then `upgrade head` | no error; `saley health` says at head |
| R1 | read-only role | insert as saley_ro | refused (read-only transaction) |
| R1 | sync | `uv run saley sync --once` on the copy, twice | second run: 0 new, 0 changed, 0 stage moves |
| R1 | stage rules | pytest tests/test_sales.py | every docs/06 §6.1 rule and precedence pair |
| R1 | metrics | `uv run saley metrics --once` | rows in metric_snapshot; rerun same day replaces |
| R1 | parity | `uv run saley worker --once parity_check` | every parity_checks row ok |
| R1 | dashboard | `uv run saley web`; open each R1 page | numbers from the copy; tiles open lists with the same count |
| R1 | dashboard parity | skill dashboard-parity | PASS report in docs/test-reports |
| R1 | discord loop | `discord send --test "@Saley ping"`, `read --test --last 3` | "[TEST] pong" in the test channel only |
| R1 | security | pytest tests/test_egress.py tests/test_web.py | linkedin.com blocked (also via redirect); no write endpoint; 401 outside dev |
