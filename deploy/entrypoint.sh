#!/bin/sh
set -eu
if [ "${1:-}" = app ]; then
    python /app/deploy/migrate.py
    # The production API is reachable only on the private Docker backend.
    # Trust Caddy's forwarded scheme so HTTPS links and redirects remain HTTPS.
    exec uvicorn punting.api:app --host 0.0.0.0 --port 8000 --no-access-log \
        --proxy-headers --forwarded-allow-ips='*'
fi
exec "$@"
