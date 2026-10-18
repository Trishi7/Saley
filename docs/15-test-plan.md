# 15 · Test plan

Acceptance per ticket. Each prompt adds its rows. The tester agent runs them.

| Ticket | Test | How | Pass |
| --- | --- | --- | --- |
| P0 | health | `uv run saley health` | prints ok, db reachable |
| P0 | discord loop | `saley discord send --test "P0 ok"` then `read --test --last 1` | the message is read back |
| P0 | env contract | pytest tests/test_env_example.py | pass |
| P0 | egress | pytest tests/test_egress.py | linkedin.com blocked, allowlist host passes, rate limit holds |
