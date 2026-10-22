#!/usr/bin/env bash
# backup.sh: one pg_dump of the Saley database, gzip, kept 14 days (R6). Run as root.
#   Nightly from cron (install.sh, 21:00 UTC = 02:30 IST, D252); deploy.sh runs it before
#   each migration with --tag pre-<git sha>.
# Output: /srv/saley/backups/saley-<UTC date>-<UTC time>[-tag].sql.gz (mode 600).
# The dump is plain SQL with owners and grants, so restore.sh rebuilds the same database.
# BACKUP_S3_URL in .env: empty = local copy only. The off-server copy is not built yet (Q-N8,
# D253): a value there makes this script stop with an error, so nobody thinks a copy exists.
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/lib.sh
. "$HERE/lib.sh"

KEEP_DAYS=14

main() {
	local tag="" db file
	while [ $# -gt 0 ]; do
		case "$1" in
		--tag)
			tag="${2:?--tag needs a name}"
			shift
			;;
		*) die "unknown option $1" ;;
		esac
		shift
	done
	[ -f "$ENV_FILE" ] || die "no $ENV_FILE"
	mkdir -p "$BACKUP_DIR"
	chmod 700 "$BACKUP_DIR"
	db="$(db_name)"
	file="$BACKUP_DIR/saley-$(date -u +%Y%m%d-%H%M%S)${tag:+-$tag}.sql.gz"

	say "pg_dump $db"
	(umask 077 && dc exec -T postgres pg_dump -U postgres --no-password -d "$db" | gzip -9 >"$file.partial") ||
		{
			rm -f "$file.partial"
			die "pg_dump failed"
		}
	gzip -t "$file.partial" || die "the dump is not a good gzip file"
	mv "$file.partial" "$file"
	find "$BACKUP_DIR" -maxdepth 1 -name 'saley-*.sql.gz' -mtime +$((KEEP_DAYS - 1)) -delete
	say "backup ok $file ($(du -h "$file" | cut -f1))"

	if [ -n "$(env_value BACKUP_S3_URL)" ]; then
		die "BACKUP_S3_URL is set, but the off-server copy is not built (Q-N8, D253). Local copy: $file"
	fi
}

main "$@"
