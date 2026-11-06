# Deployment and handover

I prepared this runbook to reproduce the local demonstration and carry the application through live deployment and recovery.

## Status and acceptance evidence

The image, local Compose services, HTTP authentication, actual cron dispatch,
Caddy configuration and an isolated backup/restore rehearsal have been verified.
The historical public-data sample also reconciles against its original CSVs.
Synthetic tests establish behavior against controlled inputs. These checks do not establish
the Professional V2 provider contract, feed completeness or a public deployment.
I recorded the executed checks in [the validation report](validation.md).

| Milestone | Delivered implementation | External acceptance still required |
| --- | --- | --- |
| 1 — Connection and ingestion | Configurable provider adapter, raw archive, CLI, repeatable projections | Use the owner's Professional API key; confirm endpoint paths/auth/schema; store and inspect actual raw responses and mapped values |
| 2 — Schema and indexing | PostgreSQL tables, migration, sectional indexes and WA query scoping | Check real provider sequential sectional fields and coverage against the source feed |
| 3 — API and filters | Authenticated JSON and HTML, threshold/trainer queries | Compare real live-feed Class Dropper/FML examples and trainer outcomes against independent calculations |
| 4 — Secure cloud access | Container, HTTPS gateway, cron, credentials and handover | Provision destination, DNS and certificate; validate from a mobile browser on an external network; verify cron and restore on that server |

No real API key, destination server or public domain was available at implementation
time. Provider egress may also require an explicit allow-list addition in a managed
environment. Never mark a milestone's live verification complete using synthetic data.

## Configuration and secrets

Generate unique credentials without printing them:

```sh
python3 scripts/init-env.py --domain racing.your-domain.example --email ops@your-domain.example
```

Use your actual domain and contact email. The script refuses to overwrite an
existing `.env` and creates the file with permissions `0600`. It generates independent
database, API and dashboard secrets; there are no production credential defaults.
Keep this file out of Git and backups shared without encryption. API tokens must
contain at least 32 characters and dashboard passwords at least 16.

The service settings are `DATABASE_URL`, `API_TOKEN`, `ADMIN_USERNAME`,
`ADMIN_PASSWORD`, `PUNTING_FORM_API_KEY`, `PROVIDER_BASE_URL`,
`PROVIDER_CONFIG_PATH` and `LOG_LEVEL`. Compose constructs `DATABASE_URL` from
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` and the private service name.
Generated database passwords use URL-safe characters; percent-encode reserved
characters if constructing your own database URL outside Compose.

`PUNTING_FORM_API_KEY` is passed only to the scheduler/ingestion service. The API
does not need the provider credential. `.env` and backup files are excluded from
the image build context. Container metadata is visible to Docker administrators,
so treat Docker access as privileged. Neither gateway nor Uvicorn access logging
is enabled; ingestion must not print request URLs containing credentials. Avoid
`docker compose config` without `--quiet` in shared logs because expanded values
include credentials. Container output rotates at 10 MB, retaining three files.

Changing `POSTGRES_PASSWORD` in `.env` does not change an already initialized
database user's password. Rotate it inside PostgreSQL as well, then recreate
dependent containers. Rotate API/dashboard secrets by changing `.env` and
recreating the app. Retain older database backups only under your access policy.

## Public HTTPS deployment

1. Provide a Linux server with Docker and Compose v2, persistent disk and backups.
   Point the domain's A/AAAA records to it. Open inbound TCP 80/443 and UDP 443
   if using HTTP/3. Restrict administrative SSH separately.
2. Copy the repository and generated `.env` to the server. Configure the actual
   provider contract before enabling ingestion. Do not use the local Compose override.
3. Run:

   ```sh
   docker compose build app
   docker compose --profile public up -d
   docker compose ps
   python3 scripts/smoke.py --url https://racing.your-domain.example
   ```

4. Open the HTTPS dashboard from a phone on an external connection, authenticate,
   and verify the table at a narrow viewport. Confirm the certificate hostname
   and that unauthenticated JSON/dashboard requests receive HTTP 401.

Caddy obtains and renews certificates, redirects HTTP to HTTPS and persists
its certificate data in `caddy_data`. A blank domain/email intentionally cannot
be used as a public deployment configuration. Only Caddy publishes public ports;
PostgreSQL and the API are on an internal network. Cron additionally has an
egress network to contact the provider. The application runs as UID/GID 10001
with a read-only filesystem and all Linux capabilities dropped.

Uvicorn trusts forwarded headers on this private backend so Caddy's HTTPS scheme
is preserved in links and redirects. Keep the API private: do not publish its port
to external interfaces or attach untrusted services to that network. The local
testing override binds only to loopback.

The app takes a PostgreSQL advisory lock, applies `alembic upgrade head`, then
starts Uvicorn. Failed migrations prevent the API from starting. Startup depends
on a healthy database; the gateway and scheduler depend on a healthy API. This
is a single-host deployment with scheduled downtime for schema changes. Back
up first, review migrations before upgrading, and record the deployed image tag
and base-image digests for repeatable releases.

## Managed cloud proxy builds

The image accepts the optional BuildKit `proxy_ca` secret. Pass a combined system
CA bundle containing the managed proxy root. Keep the inherited proxy configuration;
do not disable certificate verification or put proxy secrets in Dockerfiles.

```sh
export PROXY_CA_FILE=/etc/ssl/certs/ca-certificates.crt
docker compose -f compose.yaml -f deploy/compose.proxy.yaml build app
docker compose -f compose.yaml -f deploy/compose.proxy.yaml \
  -f deploy/compose.local.yaml up -d --wait postgres app
```

Alternatively, build directly with:

```sh
docker build --secret id=proxy_ca,src="$PROXY_CA_FILE" -t punting-warehouse:local .
```

The CA is mounted only during dependency installation and is not copied into the
image. The proxy override also mounts a CA bundle read-only for the scheduler's
HTTPS requests. Ensure the host bundle is readable by UID 10001. Ordinary hosts
with public CA trust use `compose.yaml` without this override.

## Live ingestion, daily cron and backfill

1. Obtain the Professional V2 contract/documentation and API credential. Confirm
   `config/provider.json` endpoint definitions, authentication placement, envelope
   paths, stable identifiers and field mappings against real responses. The
   canonical demo fixture is not represented as the provider's native schema.
2. Put the credential in `.env` privately. Set provider configuration/base URL
   as needed. Edit configuration before rebuilding the image; source mappings
   are baked into the image and contain no credentials.
3. Start the database/API, then ingest a known date explicitly:

   ```sh
   docker compose up -d --wait postgres app
   docker compose run --rm scheduler punting ingest-date --date 2026-11-07
   ```

4. Inspect raw response preservation and normalized values, including fields
   such as KRI, ClsDiff, FML600/FML200, RB and DLR. Resolve schema drift before
   relying on reports. Record the provider date, response counts, projection
   counts and independently calculated filter results without publishing secrets.
5. Enable cron only after that validation:

   ```sh
   docker compose --profile ingest up -d scheduler
   docker compose logs --tail=50 scheduler
   ```

`deploy/punting.cron` schedules ingestion at **05:15 Australia/Perth** every day.
The image's system timezone is Perth, so Debian cron uses local Perth time even
when the host is UTC. Perth does not observe daylight saving. Each job fetches
today and the preceding `INGEST_LOOKBACK_DAYS` days (default 7, inclusive range:
eight dates) to refresh delayed results. It performs no acquisition-time WA,
Ascot or Saturday filtering. Choose the schedule and replay window to fit the
actual provider publication times and quota; additional manual replay can refresh
later corrections. Cron does not recover missed runs while the server is stopped.

Cron runs as root only to launch jobs and redirect stdout/stderr. Each ingestion
process drops to UID/GID 10001 before calling the CLI. Configuration is retained
in a root-readable `0600` file on temporary memory storage under `/run`; it is
never written into the image. A process lock prevents scheduled jobs overlapping
within the scheduler container. Docker logs capture job errors; configure your
host monitoring to alert on failures, missing daily runs and disk usage.

Replay one date or backfill a bounded interval:

```sh
docker compose run --rm scheduler punting ingest-date --date 2026-11-07
docker compose run --rm scheduler punting ingest-range --start 2026-11-01 --end 2026-11-07
```

Always pass explicit dates for operational commands. The scheduler calculates
dates in Perth; the CLI's omitted-date default may use UTC. Replays update
relational projections idempotently and retain source history. They should not
be used to erase raw source records. Stop the scheduled service during large
manual backfills to avoid API quota competition, then re-enable it afterwards.

The synthetic local demonstration is separate:

```sh
docker compose run --rm --no-deps -v "$PWD/tests/fixtures/demo.json:/tmp/demo.json:ro" app \
  punting import-json /tmp/demo.json --endpoint synthetic-demo
```

Use a separate Compose project/database for synthetic data. If demo data was
loaded into the default local project, initialize a clean production project
and volume rather than mixing it with real data. Raw archival records are
intentionally protected from update/delete.

## Historical public data in Docker

The [public-data source guide](../data/public/README.md) describes provenance,
checksums, field mappings and coverage. With the pinned CSVs already downloaded
to `.local/public-data`, import into the running local Compose warehouse:

```sh
# These are public source files and code, not your .env credentials.
chmod -R a+rX data .local/public-data
chmod a+r scripts/import_public_data.py
docker compose run --rm --no-deps \
  -v "$PWD/scripts/import_public_data.py:/app/scripts/import_public_data.py:ro" \
  -v "$PWD/data:/app/data:ro" \
  -v "$PWD/.local/public-data:/app/.local/public-data:ro" \
  app python /app/scripts/import_public_data.py
python3 scripts/verify_public_sample.py --url http://127.0.0.1:8000
```

Adjust the URL if `LOCAL_PORT` was changed. The container revalidates the pinned
checksums before importing; it reads the supplied files without making provider
requests. This path was verified on 5 November 2026 with 18 HK meetings,
160 races and 2,007 runners. Independent CSV calculations and the trainer-backup
API agreed on 19 trainers, 59 starts, 7 wins and 75.7 gross return units.
Use `state=HK` or the dashboard's Hong Kong selection. The default WA query still
shows only WA rows. Public historical data does not validate the Professional V2
contract, Ascot coverage or unavailable KRI/FML fields.

## Backup and restore

Create an on-demand transaction-consistent PostgreSQL custom archive:

```sh
scripts/backup.sh
scripts/backup.sh backups/pre-upgrade.dump
```

The script requires the postgres service to be running. It verifies the archive
index, writes with private permissions and refuses to overwrite an existing
destination. It preserves all database tables including raw response bodies.
An archive index check is not a restore test. Encrypt off-server backups and
test restoration on a separate project. Back up `.env` and Caddy certificate
volumes separately with appropriate access controls; they are not in the DB dump.

An example host cron entry for nightly backups (adjust checkout path and ensure
this user's Docker access) is:

```cron
30 16 * * * cd /srv/punting && /srv/punting/scripts/backup.sh >> /srv/punting-backup.log 2>&1
```

This example uses a UTC host schedule: 16:30 UTC is 00:30 Perth the following
day. Use the host's actual timezone when scheduling. Define retention and offsite
copying appropriate to available storage; the script does not delete archives.

To rehearse restoration without replacing the original database, use a separate
Compose project and keep that project selected for every subsequent command:

```sh
export COMPOSE_PROJECT_NAME=punting-restore-check
export LOCAL_PORT=8001
docker compose up -d --wait postgres
scripts/restore.sh backups/pre-upgrade.dump --replace
docker compose -f compose.yaml -f deploy/compose.local.yaml up -d --wait app
python3 scripts/smoke.py --url http://127.0.0.1:8001
docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM raw_feeds;"'
```

The restore script stops gateway, scheduler and app, then restores in a single
transaction with `--clean --if-exists`. `--replace` is deliberately required:
this operation replaces the selected database contents. Take a fresh backup
before restoring in place. Do not run it against production while intending a
rehearsal. Verify raw archive counts, representative queries and authentication
before re-enabling public/ingest profiles. Unset `COMPOSE_PROJECT_NAME` and
`LOCAL_PORT` when the rehearsal is finished. Never remove a production volume
to resolve an application startup problem.

## Upgrades and troubleshooting

Back up, pull the reviewed release, build the image, then recreate the app and
the required profiles. Migrations run before readiness. A code rollback does not
reverse a database migration; use a tested restore plan for incompatible changes.
Keep credentials out of terminal transcripts and issue reports.

- **API returns 401:** use the correct Basic/Bearer scheme and inspect local secret
  configuration privately; empty or short app credentials fail closed.
- **Readiness fails:** check `docker compose ps` and app/PostgreSQL logs; check disk
  and migration status. Do not expose PostgreSQL publicly to debug it.
- **No live rows:** inspect scheduler exit/status, API credential configuration,
  endpoint contract, date and permitted egress. Synthetic success does not validate
  real provider mappings.
- **TLS issuance fails:** confirm public DNS, inbound 80/443, a real domain, ACME
  contact and outbound certificate-authority access. Do not disable TLS verification.
- **Delayed result:** rerun the date explicitly and confirm the provider published
  a settled result/price. Do not fill missing metrics with invented values.

Retain a release verification record with commit/image identifiers, migration
revision, tests run, local HTTP/browser checks, live provider evidence, public
URL/certificate checks and a dated restore rehearsal. Label each unperformed
check as pending.
