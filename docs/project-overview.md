# Project overview

## The problem I set out to solve

I wanted a racing analytics system where I could change a research rule without having to rebuild the underlying data collection. The specification focused on Western Australian meetings, especially Saturday Ascot racing, and required class-drop, closing-speed and trainer-pattern queries.

I separated data acquisition from analysis. The warehouse keeps the source evidence, while query parameters determine which records appear in the API and dashboard.

## My implementation

I used Python for ingestion, PostgreSQL for persistence and FastAPI for the read-only application. I chose server-rendered HTML for the interface so it works on a phone or desktop without storing an API token in browser JavaScript.

The schema separates meetings, races, race entries and sequential sectionals. A runner ID represents an entry in one race; the same horse can therefore appear in multiple historical starts without one result overwriting another.

Raw responses are retained independently of successful normalization. If a mapping fails or a JSON value cannot be represented in PostgreSQL JSONB, the response bytes remain available for investigation. Repeated imports append evidence and update the relational projection without duplicating race entries.

## How I checked the work

I used controlled fictional fixtures for exact KRI/FML boundaries and real historical Hong Kong data for results and payout arithmetic. I checked API authentication, HTML escaping, mobile layout, database migrations, duplicate imports, source-retention failures, scheduled execution and backup restoration.

The public-data reconciliation checks the API against calculations from the original CSVs, rather than treating a successful HTTP response as proof of correctness. The [results report](results.md) records the outcomes.

## What I would do next for a live customer deployment

1. Confirm the customer's Professional V2 contract and subscription scope.
2. Map real response envelopes and identifiers, including pagination and 200 m sectionals.
3. Reconcile a known WA meeting against the source and confirm correction/refresh behavior.
4. Configure the destination domain, TLS, secrets, backups and monitoring.
5. Validate the deployed application from an external mobile connection and record acceptance evidence.

These are the remaining integration and production acceptance steps. The repository already contains the application and operational tooling needed to carry them out.
