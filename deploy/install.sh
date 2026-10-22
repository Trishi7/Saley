#!/usr/bin/env bash
# install.sh: prepare the droplet once (R6). Run as root. Safe to run again.
#   sudo bash /srv/saley/app/deploy/install.sh            # do it
#   bash /srv/saley/app/deploy/install.sh --dry-run       # only print the steps
# What it does, in order:
#   1. check Docker and Compose are there (the droplet has Docker 29)
#   2. install ufw, fail2ban, git and python3 (apt)
#   3. firewall: allow 22, 80 and 443; deny everything else coming in
#   4. fail2ban for ssh
#   5. folders /srv/saley/secrets, /srv/saley/backups and /srv/saley/state (root only)
#   6. the Docker network saley_net
#   7. a systemd unit, so the stack starts when the server boots
#   8. the nightly backup (cron, 21:00 UTC = 02:30 IST) and its log rotation
# PM2 and its two Node apps stay as they are. Step 3 first lists every other port that listens
# on a public address; if there is one, it stops and asks you to run again with --yes, because
# the firewall would close that port.
set -Eeuo pipefail

DRY=0
YES=0
for arg in "$@"; do
	case "$arg" in
	--dry-run) DRY=1 ;;
	--yes) YES=1 ;;
	*)
		echo "unknown option $arg" >&2
		exit 1
		;;
	esac
done

ROOT_DIR="/srv/saley"
APP_DIR="$ROOT_DIR/app"
UNIT="/etc/systemd/system/saley.service"
CRON="/etc/cron.d/saley-backup"
ROTATE="/etc/logrotate.d/saley-backup"
JAIL="/etc/fail2ban/jail.d/saley.local"
# ssh: the port sshd really listens on (review L1), else 22.
SSH_PORT="$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}' || true)"
SSH_PORT="${SSH_PORT:-22}"
OPEN_PORTS="$SSH_PORT 80 443"

step() { printf '\n== %s\n' "$*"; }
run() {
	if [ "$DRY" = 1 ]; then
		printf 'would run: %s\n' "$*"
	else
		"$@"
	fi
}
# write PATH MODE: the text on stdin goes to PATH.
write() {
	if [ "$DRY" = 1 ]; then
		printf 'would write %s (mode %s):\n' "$1" "$2"
		sed 's/^/    /'
	else
		cat >"$1"
		chmod "$2" "$1"
	fi
}

if [ "$DRY" = 0 ] && [ "$(id -u)" != 0 ]; then
	echo "run as root: sudo $0" >&2
	exit 1
fi

step "1. Docker and Compose"
if [ "$DRY" = 0 ]; then
	docker --version
	docker compose version
fi

step "2. Packages"
run apt-get update -q
run env DEBIAN_FRONTEND=noninteractive apt-get install -y -q ufw fail2ban git python3

step "3. Firewall: allow $OPEN_PORTS, deny the rest"
if [ "$DRY" = 0 ]; then
	others="$(ss -ltnH 2>/dev/null | awk '{print $4}' | grep -vE '^(127\.|\[::1\]|::1)' |
		sed -E 's/.*:([0-9]+)$/\1/' | grep -vxE "$SSH_PORT|80|443" | sort -un | tr '\n' ' ' || true)"
	if [ -n "${others// /}" ] && [ "$YES" = 0 ]; then
		echo "These ports listen on a public address and the firewall would close them: $others"
		echo "If that is right (for example PM2 apps that only call out), run again with --yes."
		exit 2
	fi
fi
run ufw default deny incoming
run ufw default allow outgoing
for port in $OPEN_PORTS; do run ufw allow "$port/tcp"; done
run ufw --force enable

step "4. fail2ban for ssh"
write "$JAIL" 644 <<'EOF'
# Saley (install.sh): ban an address for 1 h after 5 failed ssh logins in 10 min.
[sshd]
enabled = true
maxretry = 5
findtime = 10m
bantime = 1h
EOF
run systemctl enable --now fail2ban
run systemctl restart fail2ban

step "5. Folders (root only)"
run install -d -m 700 "$ROOT_DIR"
for dir in secrets backups state; do run install -d -m 700 "$ROOT_DIR/$dir"; done

step "6. Docker network saley_net"
if [ "$DRY" = 1 ]; then
	echo "would run: docker network create --subnet 172.30.0.0/24 saley_net (when missing)"
else
	docker network inspect saley_net >/dev/null 2>&1 || docker network create --subnet 172.30.0.0/24 saley_net
fi

step "7. Start on boot (systemd unit saley.service)"
write "$UNIT" 644 <<EOF
# Saley v2 (install.sh): start the compose stack when the server boots.
# Containers also restart on their own (restart: unless-stopped); this unit starts them after
# a reboot even when they were stopped by hand before it.
[Unit]
Description=Saley v2 (docker compose, profile prod)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/docker compose --env-file $ROOT_DIR/.env --profile prod up -d --remove-orphans
ExecStop=/usr/bin/docker compose --env-file $ROOT_DIR/.env --profile prod stop
TimeoutStartSec=600

[Install]
WantedBy=multi-user.target
EOF
run systemctl daemon-reload
run systemctl enable saley.service

step "8. Nightly backup (21:00 UTC = 02:30 IST, keep 14 days)"
write "$CRON" 644 <<EOF
# Saley v2 (install.sh): nightly backup. The server clock is UTC.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 21 * * * root /bin/bash $APP_DIR/deploy/backup.sh >>/var/log/saley-backup.log 2>&1
EOF
write "$ROTATE" 644 <<'EOF'
/var/log/saley-backup.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
}
EOF
if [ "$DRY" = 0 ] && [ "$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)" != "UTC" ]; then
	echo "Note: the server clock is not UTC. The backup runs at 21:00 server time."
fi

step "Done. Next: put the secrets in place, then run deploy.sh (docs/14-runbook.md)."
