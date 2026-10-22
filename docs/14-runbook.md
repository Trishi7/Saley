# 14 · Runbook: Saley on the server

This page is for a person who is not a developer. Do the steps in order and type each command exactly as shown.

**Where things are:**
- The server is a DigitalOcean droplet (blr1) at **saley.membrane.social**.
- The code is in `/srv/saley/app`.
- The secrets are in `/srv/saley/.env` and `/srv/saley/secrets/`. They are never in the code folder.
- Backups are in `/srv/saley/backups`.

**Every command runs as root.** Log in, then become root:

```
ssh <your-user>@saley.membrane.social
sudo -i
cd /srv/saley/app
```

Many commands below start with `dc`. It is a short name for the long compose command. Type this line once after each login:

```
alias dc='docker compose --env-file /srv/saley/.env --profile prod'
```

The services are `caddy`, `web`, `worker`, `bot`, `news` and `postgres`.

---

## 1. First install (once)

1. Get the code:
   ```
   sudo -i
   mkdir -p /srv/saley
   git clone https://github.com/Trishi7/Saley.git /srv/saley/app
   cd /srv/saley/app
   ```
2. Prepare the server. This step sets up the firewall (ports 22, 80 and 443 only), fail2ban, the folders, the Docker network, start on boot, and the nightly backup:
   ```
   bash deploy/install.sh
   ```
   - If it stops with "These ports listen on a public address", read the list. The PM2 apps stay as they are.
   - If no person opens those ports from outside, run `bash deploy/install.sh --yes`.
3. Make the secrets file. Copy the example, then edit it:
   ```
   cp .env.example /srv/saley/.env
   chmod 600 /srv/saley/.env
   nano /srv/saley/.env
   ```
   Set these values. Make each password with `openssl rand -hex 24`. Use letters and digits only.

   | Name | Value |
   | --- | --- |
   | `SALEY_ENV` | `prod` |
   | `PUBLIC_URL` | `https://saley.membrane.social` |
   | `POSTGRES_PASSWORD` | a new password |
   | `DATABASE_URL_ADMIN` | `postgresql+psycopg://saley_admin:<password 1>@postgres:5432/saley` |
   | `DATABASE_URL` | `postgresql+psycopg://saley:<password 2>@postgres:5432/saley` |
   | `DATABASE_URL_RO` | `postgresql+psycopg://saley_ro:<password 3>@postgres:5432/saley` |
   | `AUTH_PROVIDER` | `google` |
   | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | from the Google console |
   | `GOOGLE_OAUTH_REDIRECT_URL` | `https://saley.membrane.social/auth/callback` |
   | `WEB_ALLOWED_EMAILS` | `trishi=…,sid=…,vaishnavi=…,kushal=…` |
   | `WEB_SESSION_SECRET` | `openssl rand -hex 32` |
   | `OPENALEX_MAILTO` | the team email (it is also the email for the HTTPS certificate) |
   | `GTM_SHEET_ID` | the sheet **copy** |
   | `GOOGLE_SERVICE_ACCOUNT_FILE` | leave it as is (the worker gets the key at its own path) |
   | `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_TEST_CHANNEL_ID` | the v2 bot and the v2 test channel |
   | `ANTHROPIC_API_KEY` | the API key |

   Keep these values as they are:
   - `GTM_SHEET_IS_COPY=true`
   - `TEST_MODE=true`
   - `DM_ENABLED=false`
   - `DISCORD_SALES_CHANNEL_ID=` (empty)
   - `BACKUP_S3_URL=` (empty)
4. Put the Google service account key in place:
   ```
   cp <the key file> /srv/saley/secrets/google-sa.json
   chown 10001:10001 /srv/saley/secrets/google-sa.json
   chmod 400 /srv/saley/secrets/google-sa.json
   ```
5. In the Google console, add `https://saley.membrane.social/auth/callback` as an authorised redirect URI of the OAuth client.
6. Check that the domain points at this server: `getent hosts saley.membrane.social`. It must show the droplet's address.
7. Deploy (section 2). The first deploy builds the image, which takes 5 to 10 minutes. Caddy gets the HTTPS certificate on its own.

## 2. Deploy

```
sudo -i
bash /srv/saley/app/deploy/deploy.sh
```

The script does these steps in order:
1. Gets the newest code.
2. Builds the image.
3. Sets the database roles from `.env`.
4. Takes a backup.
5. Updates the database.
6. Starts everything.
7. Waits until all services are healthy.
8. Runs the smoke checks.

The last line says **`deploy ok <code version>`**. If any step fails, the script puts the previous version back and says `ROLLED BACK`.

Run it again at any time. A second run changes nothing.

Check the site at any time, without changing anything:

```
python3 /srv/saley/app/deploy/smoke.py
```

## 3. Logs

```
cd /srv/saley/app
dc ps                       # every service and its health
dc logs -f web              # follow one service; Ctrl+C stops following
dc logs --since 1h worker   # the last hour of one service
```

Each service keeps at most 3 log files of 20 MB.

## 4. Stop and start

```
cd /srv/saley/app
dc stop             # stop everything (the data stays)
dc up -d            # start everything
dc restart bot      # restart one service
systemctl status saley
```

After a reboot, the stack starts by itself.

## 5. Change a secret

1. Edit the value: `nano /srv/saley/.env`.
2. Run `bash deploy/deploy.sh`.
   - The deploy sets the new database passwords in Postgres. It also restarts every service that reads the value.
   - `POSTGRES_PASSWORD` is different: Postgres reads it only when it first creates its data. First set the new value inside Postgres, then put the same value in `.env`, then deploy:
     ```
     dc exec postgres psql -U postgres -c "ALTER ROLE postgres PASSWORD '<new password>'"
     ```
3. Google client secret:
   - Make a new secret in the Google console.
   - Put it in `GOOGLE_OAUTH_CLIENT_SECRET`.
   - Deploy.
   - Then delete the old secret in the console.
4. Service account key:
   - Replace `/srv/saley/secrets/google-sa.json`.
   - Run the two commands from step 1.4 again.
   - Then run `dc restart worker`.
5. Discord bot token:
   - Reset it in the Discord developer portal.
   - Put it in `DISCORD_BOT_TOKEN`.
   - Deploy.

## 6. Backups and restore

- **Nightly backup:** every night at 02:30 IST (21:00 server time). The backups are kept for 14 days in `/srv/saley/backups`. The log is `/var/log/saley-backup.log`.
- **Backup now:** `bash /srv/saley/app/deploy/backup.sh`
- **Check that a backup works:** this never touches the real database.
  ```
  bash /srv/saley/app/deploy/restore.sh                       # the newest backup
  bash /srv/saley/app/deploy/restore.sh /srv/saley/backups/<file>.sql.gz
  ```
  - The script restores the file into a scratch database and checks every dashboard number against the sheet copy.
  - **`restore ok`** means the backup works.
  - A few `DIFF` lines can show if someone changed the sheet after the backup.
- **Put a backup back into the real database:** only when the data is lost or broken.
  ```
  cd /srv/saley/app
  dc stop web worker bot news
  dc exec -T postgres psql -U postgres -c "DROP DATABASE saley WITH (FORCE)" -c "CREATE DATABASE saley OWNER saley_admin"
  gunzip -c /srv/saley/backups/<file>.sql.gz | dc exec -T postgres psql -U postgres -d saley -v ON_ERROR_STOP=1 -q
  bash /srv/saley/app/deploy/deploy.sh --no-pull
  ```

## 7. Roll back to the previous version

`deploy.sh` rolls back on its own when a deploy fails. To go back by hand, check out the older code and deploy it:

```
cd /srv/saley/app
git log --oneline -5                 # find the version you want
git checkout <version>
bash deploy/deploy.sh --no-pull
```

When the newer code works again, go back to main:

```
git checkout main
bash deploy/deploy.sh
```

**The database stays at the newer version**, because updates only add to it. If the older code fails with the newer database, restore the backup that the deploy took just before it updated the database. That file is `saley-<date>-pre-<version>.sql.gz` in `/srv/saley/backups` (section 6).

## 8. The dashboard is down: what to check, in order

1. Is the server up? `ssh` works.
2. Are the services healthy? Run `dc ps`. Every line must say `healthy`. A service that says `unhealthy` or `restarting`: run `dc logs --tail 100 <service>`.
3. A deploy says "network saley_net has subnet …": the network was made by hand. Run `dc down`, then `docker network rm saley_net`, then deploy again.
4. Is there free memory and disk? Run `free -h` and `df -h /`. If the disk is full, remove old backups. Docker can also free space: `docker image prune`.
4. Is caddy up, with a valid certificate? Run `dc logs --tail 50 caddy`.
   - If you see "certificate" errors, check that the domain points at this server (section 1, step 6).
   - Ports 80 and 443 must be open: `ufw status`.
5. Does the web answer inside the server? Run `dc exec web python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8090/healthz').read())"`.
6. Does sign-in fail? Look for `auth_google_setup` and `signin_failed` in `dc logs web`. The sign-in error page names the reason.
7. Is the data old? Run `dc exec worker saley last-run sheet_sync` and read `dc logs --tail 100 worker`.
8. Start everything again: `dc up -d`. If a service still fails, deploy the previous version (section 7).
9. Still down: write the time, the `dc ps` output and the last error lines to the team.

## 9. Laptop (developers)

- See README "Run on a laptop".
- Roles on a laptop: `psql -U postgres -f deploy/sql/roles.sql`.
- Migrate: `uv run saley db-upgrade`.
- Check: `uv run saley health`.
