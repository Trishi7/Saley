# Shared by deploy.sh, backup.sh and restore.sh. Sourced, never run on its own.
# Paths are the server layout (R6 brief). The SALEY_* names below are for CI and tests only;
# the server uses the defaults. Nothing here prints a secret.

APP_DIR="${SALEY_APP_DIR:-/srv/saley/app}"
ENV_FILE="${SALEY_ENV_FILE:-/srv/saley/.env}"
BACKUP_DIR="${SALEY_BACKUP_DIR:-/srv/saley/backups}"
STATE_DIR="${SALEY_STATE_DIR:-/srv/saley/state}"
# CI adds deploy/compose.ci.yml (D241). The server uses the one file.
COMPOSE_FILES="${SALEY_COMPOSE_FILES:-docker-compose.yml}"
KEY_FILE="${GOOGLE_SERVICE_ACCOUNT_HOST_FILE:-/srv/saley/secrets/google-sa.json}"
APP_UID=10001 # the `saley` user of the image (Dockerfile)
SERVICES="postgres web worker bot news caddy"
HEALTH_WAIT="${SALEY_HEALTH_WAIT:-300}" # seconds; CI uses less for the rollback check
export SALEY_ENV_FILE="$ENV_FILE"

say() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() {
	say "FAIL: $*" >&2
	exit 1
}

# docker compose with the server files, the env file and the prod profile.
dc() {
	local args=() f
	for f in $COMPOSE_FILES; do args+=(-f "$APP_DIR/$f"); done
	docker compose --project-directory "$APP_DIR" "${args[@]}" --env-file "$ENV_FILE" --profile prod "$@"
}

# The value of KEY in the env file. Only for names, URLs and the database name: the scripts
# never echo a value that holds a password.
env_value() {
	sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

# The database name at the end of DATABASE_URL (default saley).
db_name() {
	local url name
	url="$(env_value DATABASE_URL)"
	name="${url##*/}"
	name="${name%%\?*}"
	printf '%s' "${name:-saley}"
}

NET_SUBNET="172.30.0.0/24" # D254: caddy is 172.30.0.2 on it (docker-compose.yml)

ensure_network() {
	local subnet
	if ! docker network inspect saley_net >/dev/null 2>&1; then
		docker network create --subnet "$NET_SUBNET" saley_net >/dev/null
		return
	fi
	subnet="$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' saley_net)"
	[ "$subnet" = "$NET_SUBNET" ] || die "network saley_net has subnet $subnet, want $NET_SUBNET"
}

# The env file holds every secret: root only (review M4). Tests on Windows skip it.
check_env_mode() {
	local mode
	[ "${SALEY_SKIP_MODE_CHECK:-0}" = 1 ] && return 0
	mode="$(stat -c %a "$ENV_FILE")"
	case "$mode" in
	600 | 400) ;;
	*) die "$ENV_FILE has mode $mode; run: chmod 600 $ENV_FILE" ;;
	esac
}

# The worker mounts the key; a missing key stops the deploy with a plain message (review H2).
check_key_file() {
	[ -f "$KEY_FILE" ] || die "no service account key at $KEY_FILE (runbook: first install, step 4)"
}

# The image runs as uid 10001; a key file owned by root would be unreadable in the container.
fix_key_owner() {
	if [ -f "$KEY_FILE" ]; then
		chown "$APP_UID:$APP_UID" "$KEY_FILE" 2>/dev/null ||
			say "warning: cannot give $KEY_FILE to uid $APP_UID; the worker may not read it"
		chmod 400 "$KEY_FILE" 2>/dev/null || true
	fi
}

# The health of one service: healthy, starting, unhealthy, or "missing".
health_of() {
	local cid
	cid="$(dc ps -q "$1" 2>/dev/null || true)"
	if [ -z "$cid" ]; then
		printf 'missing'
		return
	fi
	docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid"
}

# Wait until every service is healthy. $1 = seconds (default 300). Names the first bad one.
wait_healthy() {
	local limit="${1:-$HEALTH_WAIT}" start=$SECONDS svc state bad
	while :; do
		bad=""
		for svc in $SERVICES; do
			state="$(health_of "$svc")"
			if [ "$state" != "healthy" ]; then
				bad="$svc ($state)"
				break
			fi
		done
		if [ -z "$bad" ]; then
			say "all services healthy"
			return 0
		fi
		if [ $((SECONDS - start)) -ge "$limit" ]; then
			say "not healthy after ${limit}s: $bad"
			return 1
		fi
		sleep 5
	done
}
