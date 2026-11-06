# Results and delivery status

I reran the full suite from this repository on **6 November 2026**: **107 passed, none failed or skipped**, with **90% statement coverage** and the same upstream deprecation warning. The [test transcript](evidence/test-run.txt) and [coverage summary](evidence/coverage-summary.json) are included.

I validated the implementation with controlled test cases, PostgreSQL integration checks, browser checks and a real historical dataset. This report separates what I demonstrated from the external acceptance steps that remain.

## Recorded validation

The original full validation on **5 November 2026** produced **107 passing tests**, with no failures or skipped tests when PostgreSQL integration was enabled. Statement coverage was **90%**. One upstream Starlette TestClient deprecation warning was recorded.

| Check | Recorded result |
| --- | --- |
| Source retention | Original bytes survive malformed/compressed responses, decoder failures and unsupported JSONB values |
| Database | Migrations, populated upgrade, foreign keys, indexes, immutable archive and replay checks passed |
| Query rules | Threshold boundaries, null metrics, regional/date filters, pagination and sectional order passed |
| Financial arithmetic | Settled starts, scratches, disqualifications, missing prices, dead heats and actual payouts tested |
| Authentication | Unauthorized requests rejected; authenticated HTML and API requests passed |
| Browser | Chromium desktop 1440×1000 and mobile 390×844 passed layout, filter and sectional checks |
| Deployment | Docker build/startup, non-root API, cron dispatch, proxy scheme handling and Caddy configuration checked |
| Recovery | Separate backup/restore rehearsal preserved counts and passed application smoke checks |

The [technical validation record](validation.md) provides commands and further detail. A GitHub Actions workflow is included; these recorded results are local execution evidence, not a claim about a particular remote Actions run.

## Real historical dataset

I imported the latest 160 complete races from the pinned Hong Kong source for a manageable demonstration. The original CSVs were archived in full.

| Sample measure | Result |
| --- | ---: |
| Meetings | 18 |
| Races | 160 |
| Runner entries | 2,007 |
| Sample dates | 29 June–28 August 2005 |
| Entries with a previous observed start | 1,897 |
| Recorded winner payouts | 160 |

For the seven-day backup cohort, independent source-CSV calculations matched the API for **19 trainers, 59 starts, seven wins and 75.7 gross return units**. These are historical reconciliation figures; I have not presented them as a validated profitable strategy.

Horse, trainer and jockey identities are anonymized source IDs. DLR means the gap since the previous start observed in the input files, not proof of complete career history. KRI, FML, RB and uniform 200 m sectionals are unavailable in this source and remain null. I use labelled fictional WA fixtures to test those parts of the specification.

## Milestone status

| Milestone | What I delivered and checked | What remains |
| --- | --- | --- |
| 1 — Connection and ingestion | Configurable HTTP ingestion, raw archive and actual CSV imports into PostgreSQL | Live Professional V2 authentication, endpoint/pagination contract and source dump |
| 2 — Database and sectionals | Relational tables, variables, ordered sectional indexes, migrations and WA query scope | Reconcile actual provider sectionals and benchmark fields |
| 3 — Analytical API | Requested parameterized filters, authentication, exact fixture results and real trainer reconciliation | Validate live WA KRI/FML examples against the subscribed feed |
| 4 — Secure deployment | Built Docker stack, browser interface, cron, HTTPS configuration and tested backup/restore | Selected server/domain, public DNS/TLS issuance and external mobile acceptance |

## Review findings I addressed

Validation uncovered raw-response loss on certain decoder failures, disqualified starts omitted from stakes, missing request-date mapping context and a mobile overflow issue. I corrected them and added regression checks. Additional tests cover duplicate source horse entries and numeric winner representations.

## How to inspect the result

1. Follow the README to start the fictional WA demonstration.
2. Try each report and change its thresholds or date/track filters.
3. Open the sectional view for Demo Western Comet.
4. Import the public sample using the source guide and select Hong Kong / quick backup.
5. Run the independent CSV reconciliation script.
6. Review the tests, original specification and deployment runbook alongside the milestone table above.
