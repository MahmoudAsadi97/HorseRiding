#!/bin/sh
# Explicit replacement only. Prefer testing in a separate Compose project first.
set -eu
cd "$(dirname "$0")/.."
if [ "$#" -ne 2 ] || [ "$2" != --replace ]; then
    echo 'Usage: scripts/restore.sh BACKUP.dump --replace' >&2
    echo 'This replaces the configured database. Take a fresh backup first.' >&2
    exit 2
fi
test -r "$1"
docker compose exec -T postgres pg_restore --list < "$1" > /dev/null
docker compose stop scheduler gateway app
docker compose exec -T postgres sh -c \
    'exec pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    < "$1"
echo 'Database restored. Run docker compose up -d app, smoke-test, then re-enable required profiles.'
