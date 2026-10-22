#!/usr/bin/env bash
# restore.sh: prove a backup works (R6). Run as root.
#   sudo bash /srv/saley/app/deploy/restore.sh                 # the newest backup
#   sudo bash /srv/saley/app/deploy/restore.sh /srv/saley/backups/saley-20261023-210000.sql.gz
# It restores the file into a SCRATCH database (saley_restore_check), never into `saley`, then
# runs `saley sheet verify` against it: every dashboard number from the restored database must
# match the sheet copy. A sheet that changed since the backup can show DIFF lines; the next
# worker sync fixes those in the real database. The scratch database is dropped at the end
# (--keep keeps it). To put a backup back into the real database, see the runbook.
#
# Options (CI and tests): --raw-file PATH  verify against a saved sheet (JSON), not Google (D249)
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/lib.sh
. "$HERE/lib.sh"

SCRATCH="saley_restore_check"

psql_admin() {
	dc exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 -q "$@"
}

main() {
	local file="" raw="" keep=0 verify_args=() mount=()
	while [ $# -gt 0 ]; do
		case "$1" in
		--raw-file)
			raw="${2:?--raw-file needs a path}"
			shift
			;;
		--keep) keep=1 ;;
		-*) die "unknown option $1" ;;
		*) file="$1" ;;
		esac
		shift
	done
	[ -f "$ENV_FILE" ] || die "no $ENV_FILE"
	if [ -z "$file" ]; then
		file="$(ls -1t "$BACKUP_DIR"/saley-*.sql.gz 2>/dev/null | head -n 1 || true)"
	fi
	[ -n "$file" ] && [ -f "$file" ] || die "no backup file (looked in $BACKUP_DIR)"
	gzip -t "$file" || die "$file is not a good gzip file"

	say "restore $(basename "$file") into $SCRATCH"
	if [ "$keep" = 0 ]; then
		# A failed restore must not leave a full copy of the data behind (review L5).
		trap 'psql_admin -c "DROP DATABASE IF EXISTS $SCRATCH WITH (FORCE)" || true' EXIT
	fi
	psql_admin -c "DROP DATABASE IF EXISTS $SCRATCH WITH (FORCE)" -c "CREATE DATABASE $SCRATCH OWNER saley_admin"
	gunzip -c "$file" | psql_admin -d "$SCRATCH" >/dev/null || die "the restore failed"
	psql_admin -c "GRANT CONNECT ON DATABASE $SCRATCH TO saley, saley_ro"

	say "sheet verify against $SCRATCH"
	verify_args=(saley sheet verify --database "$SCRATCH")
	if [ -n "$raw" ]; then
		mount=(-v "$raw:/run/raw/sheet.json:ro")
		verify_args+=(--raw-file /run/raw/sheet.json)
	fi
	# A one-off container, no port: the mock sign-in of `sheet verify` stays in this process.
	if dc run --rm --no-deps -e SALEY_ENV=dev -e AUTH_PROVIDER=mock "${mount[@]}" worker "${verify_args[@]}"; then
		say "restore ok: sheet verify PASS on $SCRATCH"
		status=0
	else
		say "restore FAIL: sheet verify did not pass on $SCRATCH"
		status=1
	fi
	return "$status"
}

status=1
main "$@"
