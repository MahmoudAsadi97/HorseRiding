#!/bin/sh
# Produces a transaction-consistent custom PostgreSQL archive, private to its owner.
set -eu
umask 077
cd "$(dirname "$0")/.."
destination=${1:-backups/punting-$(date -u +%Y%m%dT%H%M%SZ).dump}
mkdir -p "$(dirname "$destination")"
temporary=$(mktemp "$(dirname "$destination")/.punting-backup.XXXXXXXX")
trap 'rm -f "$temporary"' EXIT HUP INT TERM
docker compose exec -T postgres sh -c \
    'exec pg_dump --format=custom --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    > "$temporary"
docker compose exec -T postgres pg_restore --list < "$temporary" > /dev/null
# Hard-link publication is atomic and refuses to replace an existing backup.
ln "$temporary" "$destination"
printf 'Backup written and archive index verified: %s\n' "$destination"
