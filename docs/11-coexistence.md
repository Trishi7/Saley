# 11 · v1 and v2 side by side

No cutover plan is needed now. The bots are different apps on different channels.

| | v1 Saley | v2 Saley |
| --- | --- | --- |
| App | Trishi7/sales-bot, PM2 on Hetzner | Trishi7/Saley, laptop now, Docker later |
| Channel | API Sales, saley-test | saley-test-v2 only |
| Sheet | live GTM Playbook, writes after approval | copy of GTM Playbook, writes after approval |
| DB | SQLite | PostgreSQL |

Rules while both run:
- v2 never posts to API Sales. `DISCORD_SALES_CHANNEL_ID` stays empty until a decision says otherwise.
- v2 reads the live sheet only when a decision says so. Until then, the copy.
- When v2 replaces a v1 rule in prod, the v1 rule is switched off in v1 config. One owner per rule.
