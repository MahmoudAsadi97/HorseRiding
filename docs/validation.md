# Validation and milestone handover

I reran the full suite from this repository on **6 November 2026**: **107 passed, none failed or skipped**, with **90% statement coverage** and the same upstream deprecation warning. The [test transcript](evidence/test-run.txt) and [coverage summary](evidence/coverage-summary.json) are included.

I completed the original validation on 5 November 2026 using Python 3.12 and PostgreSQL 17. This customer-facing edition is maintained in `MahmoudAsadi97/HorseRiding` on `main`.

## Results

- **107 tests passed, none failed or skipped** with `TEST_DATABASE_URL` configured.
  `pytest --tb=short -q --cov=punting` measured **90% statement coverage**.
  One upstream Starlette warning reports future deprecation of its httpx-based
  TestClient. It did not fail tests; the application HTTP client is separately tested.
- Credential-bearing smoke/reconciliation helpers reject redirects and unsafe service origins; browser credentials are restricted to the selected origin.
- Ruff checks pass; dependency compatibility check passes; Python wheel builds.
- PostgreSQL: empty and repeated migration, populated-schema upgrade, JSONB/BYTEA,
  foreign keys, sequential indexes, duplicate replay and immutable archive checks.
- Invalid/truncated gzip, arbitrary decoder exceptions, nonfinite JSON numbers,
  escaped NUL and excessive JSON exponents retain original response bytes and
  record failed normalization rather than losing source evidence.
- Provider adapter: bounded transient retries, preserved intermediate/partial
  response bytes, HTTPS-only origin, authentication redaction, configured field
  mapping, request-date context and independent meeting failure handling.
- API: exact class/KRI/FML boundaries, missing metrics, regional/date/Saturday
  scope, stable pagination, ordered sectionals, unauthenticated rejection,
  fail-closed missing credentials, escaped HTML and security headers.
- Trainer arithmetic: settled starts, DNF and disqualified losses, scratches,
  first-up/backup boundaries, missing prices, dead heats and published payouts.
- Real HTTP smoke: health, unauthorized rejection, authenticated dashboard and
  all four analytical endpoints passed. Playwright with system Chromium passed
  at **1440×1000** and **390×844**, including form submission and six sectional
  rows. Screenshots are in `docs/screenshots/` and contain fictional WA records.
- Docker image builds from hash-locked dependencies; app runs as UID 10001;
  Compose database/application health and repeat startup pass. Caddy configuration
  validates. A real cron minute dispatch in an isolated test container verified
  the Perth date window and UID 10001 ingestion execution.
- Backup and restore scripts were exercised against separate test containers;
  restored raw feed and runner counts matched, and the restored application
  passed the same authenticated HTTP smoke tests.

## Real historical data reconciliation

The public HK source has 6,349 races and 79,447 runner entries from 1997–2005.
The retained, checksum-verified source pair is about 11.5 MB. The default projection
contains **18 meetings, 160 races and 2,007 runners**, dated **29 June–28 August 2005**.
There are 1,897 runners with a previous observed start date and 160 recorded
winner payouts. The full input history is used to calculate observed DLR before
selecting the recent sample.

`scripts/verify_public_sample.py` independently reads the original CSVs and
reconciles every trainer's API result. The seven-day cohort is **19 trainers,
59 starts, seven wins and 75.7 gross return units**. These are reconciliation
figures for a historical sample, not an out-of-sample strategy evaluation.

The HK source does not contain KRI, FML, RB or uniform horse-level 200m sections.
Those fields remain null. Consequently real HK first-up/class-dropper/elite-closer
matches are absent; explicitly synthetic fixtures validate those exact rules.
No source metric is invented. See [provenance and limitations](../data/public/README.md).
Source data is downloaded on demand and kept outside Git because original dataset
redistribution terms could not be independently confirmed.

## Milestone acceptance

| Milestone | Verified delivery | Remaining external acceptance |
| --- | --- | --- |
| 1 — API ingestion | HTTP adapter, archive-first ETL, CLI, synthetic JSON and real public CSV ingestion into PostgreSQL | Punting Form Professional credential, confirmed V2 endpoints/fields and pagination contract, live dump |
| 2 — schema | Required relational entities/variables, sectional indexes, immutable raw archive, migrations, WA query defaults | Real provider 200m and benchmark field mappings need subscriber data |
| 3 — query API | All requested configurable filters; correct synthetic boundary arrays; real HK backup arithmetic; authenticated HTTP checks | Live WA KRI/FML acceptance needs proprietary source fields |
| 4 — deployment | Built container stack, non-root app, auth, responsive UI, cron, HTTPS config, backup/restore, CI definition and handover | Public server/domain/DNS, trusted certificate issuance and remote mobile access verification |

**Software is validated locally; the specification's live-provider and public
production acceptance requirements are not claimed complete.** No destination
server or domain was supplied. A CI workflow definition does not prove that a remote GitHub Actions run has completed.

## Independent review and fixes

A fresh reviewer reproduced three important defects: source loss on decoding or
JSONB conversion errors, disqualified runners excluded from ROI stakes, and
missing date context in ingestion mapping. Each was fixed with a regression that
failed first. Additional checks corrected mobile overflow, documented populated
migration upgrades, and rejected duplicate horses or inconsistent numeric winner
representations in the public adapter. Final full-suite results above include
these fixes.

## My design decisions

- I chose Python/FastAPI/PostgreSQL, with SQLAlchemy and Alembic for persistence and migrations.
- Retain all acquired geographies; default to WA at query time. Otherwise future
  filter changes would require acquiring discarded source records.
- Use a configurable provider adapter until the subscriber's actual contract is
  verified. Guessing endpoints or proprietary variables could silently lose data.
- Use historical HK public data for real arithmetic checks, while retaining
  labelled fictional WA fixtures for unavailable proprietary metrics.
- Use separate closing odds and gross settled payout fields; published payout
  overrides an odds estimate and must not receive a second dead-heat adjustment.
- Generated secrets and source CSVs stay outside Git. The repo contains all source,
  tests, deployment files, specification, source manifest and reproducible import.

## Repeat checks

```sh
.venv/bin/ruff check .
# Set TEST_DATABASE_URL privately to a disposable PostgreSQL admin connection.
.venv/bin/pytest --tb=short -q --cov=punting
.venv/bin/python scripts/smoke.py --url http://127.0.0.1:8001
.venv/bin/python scripts/verify_public_sample.py --url http://127.0.0.1:8001
# Optional: install playwright in a separate venv; /usr/bin/chromium is required.
python scripts/browser_smoke.py --url http://127.0.0.1:8001
```

Browser validation expects the included synthetic fixture in addition to any HK
sample. Without TEST_DATABASE_URL, PostgreSQL tests explicitly skip and that run
must not be described as full database validation.
