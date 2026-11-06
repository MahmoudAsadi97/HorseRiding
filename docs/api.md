# API guide

I expose read-only research endpoints through FastAPI. All `/api/` routes require
`Authorization: Bearer <API_TOKEN>`. The browser dashboard uses separate HTTP Basic credentials. Generate credentials with `scripts/init-env.py` and keep `.env` private. Use HTTPS for remote access.

## Endpoints

| Method and path | Response |
| --- | --- |
| `GET /api/meetings` | Scoped meeting records |
| `GET /api/class-droppers` | Entries satisfying class-change and KRI thresholds |
| `GET /api/elite-closers` | Entries satisfying FML200 and running-style filters |
| `GET /api/trainers/first-up` | Trainer report for `RB == 0` |
| `GET /api/trainers/backup` | Trainer report for `0 <= DLR <= max_dlr` |
| `GET /api/runners/{runner_id}/sectionals` | Runner details and sectionals in sequence order |
| `GET /health/live` | Public process health |
| `GET /health/ready` | Public database connectivity check |

Responses use `items`, `total`, `limit` and `offset`. Trainer responses include methodology text. Sectional responses also contain `runner`. Public interactive API documentation is disabled; this document describes the supported interface.

## Parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `state` | `WA` | Region; use `HK` for the public sample or `ALL` for all acquired regions |
| `track` | unset | Case-insensitive exact track name |
| `date_from`, `date_to` | unset | Inclusive meeting-date range, ISO `YYYY-MM-DD` |
| `saturday_only` | `false` | Restrict meeting dates to Saturdays |
| `limit` | `100` | Page size, 1–500 |
| `offset` | `0` | Nonnegative page offset, at most 1,000,000 |
| `cls_diff_max` | `0` | Strict upper bound: `ClsDiff < cls_diff_max` |
| `kri_min` | `70` | Inclusive KRI minimum, within 0–100 |
| `fml200_min` | `2.5` | Inclusive FML200 minimum |
| `run_styles` | `Mid-Field`, `Backmarker` | Repeat query key to supply multiple styles |
| `min_starts` | `1` | Minimum qualifying trainer sample size |
| `max_dlr` | `7` | Inclusive backup window, 0–365 days |

Examples:

```text
/api/class-droppers?state=WA&track=Ascot&saturday_only=true&kri_min=70
/api/elite-closers?fml200_min=2.5&run_styles=Mid-Field&run_styles=Backmarker
/api/trainers/backup?state=HK&max_dlr=7&min_starts=2
/api/runners/demo-wa-r1-h1/sectionals?state=WA
```

## Trainer calculation

I count settled finished, DNF and disqualified entries, excluding scratches and pending/provisional results. A positive finishing position is the fallback when explicit settlement status is absent. Entries without a trainer are excluded from grouping.

- Strike rate = wins / settled starts × 100.
- Priced starts have a recorded decimal price of at least 1.
- Return units use the published winning return when present, otherwise decimal price × dead-heat factor.
- Profit units = total return units − priced starts, using a flat one-unit stake.
- ROI = profit units / priced starts × 100; no priced starts produces `null` ROI.

Each dead-heat winner counts as a win for strike rate. A published payout already reflects settlement and is not discounted again. Responses expose starts, wins, priced/unpriced starts, priced wins, return units, profit units, strike rate and ROI so the denominator is visible.

## Errors and missing data

- `401`: missing or invalid authentication.
- `422`: invalid or inconsistent query parameters.
- `404`: runner not found within the requested scope.
- `503`: authentication not configured or database unavailable.

An empty `items` list is a valid result. Null analytical metrics do not qualify for numeric filters. HK class-dropper, elite-closer and first-up results are empty because the public source lacks the necessary proprietary fields; they are not filled with estimates.
