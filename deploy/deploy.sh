#!/usr/bin/env bash
# deploy.sh: put the newest main on the server (R6). Run as root:
#   sudo bash /srv/saley/app/deploy/deploy.sh
# Steps, in order:
#   git pull -> build the image -> roles from .env (saley db-roles) -> backup before the
#   migration -> migrate as saley_admin -> grants again -> compose up -> wait healthy ->
#   smoke (deploy/smoke.py) -> "deploy ok <git sha>".
# Any failure after the build puts the previous image back (D248), waits for it to be healthy
# and says so. The database stays at the newest migration: migrations only add. To go back to
# the old schema too, restore the backup taken before the migration (runbook: roll back).
# Safe to run twice: a second run changes nothing and ends with "deploy ok <git sha>".
#
# Options (CI and tests; the server needs none):
#   --no-pull       use the checkout as it is
#   --image REF     deploy this existing image instead of building one
#   --skip LIST     smoke checks to skip, for example "sheet,bot" (CI has no sheet and no Discord)
#
# The whole script is one function, read before it runs: a `git pull` that changes this file
# cannot change the steps of the run that pulled it.
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/lib.sh
. "$HERE/lib.sh"

rollback() {
	local why="$1"
	if [ -n "$PREV" ]; then
		say "rolling back to the previous image ($why)"
		docker tag "$PREV" saley:current
		dc up -d --remove-orphans || true
		if wait_healthy "$HEALTH_WAIT"; then
			say "ROLLED BACK: the previous image runs again. The database stays at the new migration head."
		else
			say "ROLLBACK NOT HEALTHY: follow the runbook section 'The dashboard is down'."
		fi
	else
		say "no previous image: nothing to roll back to"
	fi
	die "deploy of $SHA failed: $why"
}

main() {
	local pull=1 image="" skip=""
	while [ $# -gt 0 ]; do
		case "$1" in
		--no-pull) pull=0 ;;
		--image)
			image="${2:?--image needs an image name}"
			shift
			;;
		--skip)
			skip="${2:?--skip needs a list}"
			shift
			;;
		*) die "unknown option $1 (see the top of deploy.sh)" ;;
		esac
		shift
	done

	[ -f "$ENV_FILE" ] || die "no $ENV_FILE (runbook: first install)"
	check_env_mode
	check_key_file
	cd "$APP_DIR"
	mkdir -p "$STATE_DIR" "$BACKUP_DIR"
	if command -v flock >/dev/null 2>&1; then
		exec 9>"$STATE_DIR/deploy.lock"
		flock -n 9 || die "another deploy is running"
	else
		say "note: no flock on this machine, so two deploys at once are not stopped"
	fi

	if [ "$pull" = 1 ]; then
		say "git pull"
		git pull --ff-only
	fi
	SHA="$(git rev-parse --short=12 HEAD)"
	PREV="$(docker image inspect -f '{{.Id}}' saley:current 2>/dev/null || true)"

	ensure_network
	fix_key_owner

	if [ -n "$image" ]; then
		say "use image $image"
		docker image inspect "$image" >/dev/null 2>&1 || die "no image $image"
		docker tag "$image" saley:current
	else
		say "build saley:$SHA"
		dc build migrate || die "the build failed; nothing changed on the server"
		docker tag saley:current "saley:$SHA"
	fi

	say "postgres"
	# --wait: a fresh volume runs initdb first; db-roles must not race it (review HIGH).
	dc up -d --wait postgres || rollback "postgres did not become healthy"
	say "roles (saley db-roles)"
	dc run --rm --no-deps migrate saley db-roles || rollback "db-roles"
	say "backup before the migration"
	bash "$HERE/backup.sh" --tag "pre-$SHA" --local-only || rollback "backup"
	say "migrate (alembic upgrade head as saley_admin)"
	dc run --rm --no-deps migrate || rollback "migrate"
	dc run --rm --no-deps migrate saley db-roles >/dev/null || rollback "grants after the migration"
	say "compose up"
	dc up -d --remove-orphans || rollback "compose up"
	wait_healthy "$HEALTH_WAIT" || rollback "not healthy"
	say "smoke"
	python3 "$HERE/smoke.py" ${skip:+--skip "$skip"} || rollback "smoke"
	printf '%s\n' "$SHA" >"$STATE_DIR/current"
	prune_images
	say "deploy ok $SHA"
}

# Keep the newest KEEP_IMAGES saley:<sha> images and the one in use; drop the rest (review L4).
KEEP_IMAGES=3
prune_images() {
	local current tag id old old_tags
	current="$(docker image inspect -f '{{.Id}}' saley:current 2>/dev/null || true)"
	# `|| true`: no old image is normal; grep's exit 1 must not end a good deploy (pipefail).
	old_tags="$(docker image ls saley --format '{{.Tag}} {{.ID}}' | grep -v -e '^current ' -e '^broken ' |
		tail -n +$((KEEP_IMAGES + 1)) || true)"
	printf '%s\n' "$old_tags" | while read -r tag id; do
		[ -n "$tag" ] || continue
		old="$(docker image inspect -f '{{.Id}}' "saley:$tag" 2>/dev/null || true)"
		if [ -n "$old" ] && [ "$old" != "$current" ]; then
			docker image rm "saley:$tag" >/dev/null 2>&1 || true
		fi
	done
	docker image prune -f >/dev/null 2>&1 || true
}

SHA=""
PREV=""
main "$@"
exit $?
