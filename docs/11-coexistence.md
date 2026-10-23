# 11 · v1 and v2 side by side

v1 keeps running until the human decides the cut-over. The two bots are different apps, and each has its own channels.

| | v1 Saley (sales-bot) | v2 Saley |
| --- | --- | --- |
| App | Trishi7/sales-bot, PM2 | Trishi7/Saley, Docker Compose (R6) |
| Server | the droplet, under PM2 (two Node apps) | the same droplet, in `/srv/saley`, behind caddy on saley.membrane.social |
| Channel | API Sales, saley-test | the v2 test channel only (`DISCORD_TEST_CHANNEL_ID`) |
| Discord bot | the v1 bot token | its own bot token; never the v1 token |
| Sheet | live GTM Playbook, writes after approval | the copy of the GTM Playbook (`GTM_SHEET_IS_COPY=true`), writes after approval |
| DB | SQLite | PostgreSQL (the compose `postgres`, never published) |
| Ports | PM2 apps only; nothing on 80, 443, 5432, 8090 | caddy alone publishes 80 and 443 |

**Rules while both run:**
- **v1 stays under PM2 until the cut-over decision.** R6 does not stop it, restart it or change it. `deploy/install.sh` leaves PM2 and its apps as they are.
- **The two bots never share a channel:**
  - v2 posts only to its own test channel (`TEST_MODE=true`).
  - `DISCORD_SALES_CHANNEL_ID` stays empty on the server, and the bot refuses to start when the test channel equals the sales channel.
  - The v2 test channel is not one that v1 posts in.
- v2 never posts to API Sales. DMs stay off (`DM_ENABLED=false`).
- v2 reads the live sheet only when a decision says so. Until then, it reads the copy.
- When v2 takes over a v1 rule in prod, the v1 rule is switched off in the v1 config. Each rule has one owner.

**Cut-over of the bot to API Sales (R7 rollout, docs/17 §11; each step needs the human's go):**
1. R7 has GATE: PASS on the laptop with `TEST_MODE=true`. The human commits, pushes and deploys (docs/14 section 2). On the server `TEST_MODE=true` stays.
2. One week in the v2 test channel. The team presses real buttons there. Check every day: `dc exec bot saley bot status`.
3. When the human says go (a decision in DECISIONS.md):
   - on the droplet, stop the v1 bot: `pm2 stop sales-bot`, then `pm2 save`;
   - in `/srv/saley/.env` set `DISCORD_SALES_CHANNEL_ID=<the API Sales channel id>` and `TEST_MODE=false`;
   - deploy again; the bot refuses to start when the test channel equals the sales channel;
   - check one card in API Sales, and the health card in the test channel at 23:45.
4. DMs stay off until Trishi says on (`DM_ENABLED`, `DM_USERS`).
5. Back out: set `TEST_MODE=true` and `DISCORD_SALES_CHANNEL_ID=` again, deploy, then `pm2 start sales-bot`.

The live sheet is a separate decision: v2 reads and writes the copy until then.
