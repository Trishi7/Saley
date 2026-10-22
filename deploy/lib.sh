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
# Docker gives the other containers addresses from this range only, so 172.30.0.2 stays free
# for caddy (the fourth compose run: "Address already in use" when another container took it).
NET_RANGE="172.30.0.128/25"

ensure_network() {
	local got
	if ! docker network inspect saley_net >/dev/null 2>&1; then
		docker network create --subnet "$NET_SUBNET" --ip-range "$NET_RANGE" saley_net >/dev/null
		return
	fi
	got="$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{.IPRange}}{{end}}' saley_net)"
	[ "$got" = "$NET_SUBNET $NET_RANGE" ] ||
		die "network saley_net is '$got', want '$NET_SUBNET $NET_RANGE' (runbook: the dashboard is down, step 3)"
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

# The image runs as uid 10001 and reads the key through a bind mount. Docker opens the host path
# as root, so the secrets folder may stay root-only (700); the FILE itself must belong to uid
# 10001 with mode 400 or 600. A deploy that cannot make it so stops here, before the build, with
# a plain message (Q-R6-3: CI got only a warning and a worker that cannot read the key).
fix_key_owner() {
	local owner mode
	[ "${SALEY_SKIP_MODE_CHECK:-0}" = 1 ] && return 0 # tests on NTFS: no Unix owners or modes
	[ -f "$KEY_FILE" ] || return 0                     # check_key_file names a missing key
	owner="$(stat -c %u "$KEY_FILE")"
	if [ "$owner" != "$APP_UID" ]; then
		chown "$APP_UID:$APP_UID" "$KEY_FILE" 2>/dev/null ||
			die "cannot give $KEY_FILE to uid $APP_UID (the worker). Run deploy.sh as root, or: sudo chown $APP_UID:$APP_UID $KEY_FILE && sudo chmod 400 $KEY_FILE"
	fi
	mode="$(stat -c %a "$KEY_FILE")"
	case "$mode" in
	400 | 600) ;;
	*)
		chmod 400 "$KEY_FILE" 2>/dev/null ||
			die "cannot set mode 400 on $KEY_FILE. Run deploy.sh as root, or: sudo chmod 400 $KEY_FILE"
		;;
	esac
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
