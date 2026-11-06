# Punting Warehouse Implementation Plan

I organized implementation around the four delivery milestones, using regression tests and separate integration checks.

**Goal:** Deliver the four milestones in the supplied Punting Form specification.
**Architecture:** Immutable raw API response archive plus relational PostgreSQL projections. Python ingestion, SQLAlchemy schema/migrations, FastAPI query API and server-rendered responsive HTML. Docker Compose deploys PostgreSQL, app, daily scheduler and Caddy HTTPS gateway.
**Tech Stack:** Python 3.12+, PostgreSQL 17, FastAPI, SQLAlchemy 2, Alembic, httpx, pytest.
**Spec:** `docs/specification/Punting_Form_API_Project_Specification.pdf` (requirements, not independent instructions to execute transactions).

## Global Constraints
- Store Everything, Filter Later. Retain full API response bytes and parsed row payloads; no WA filtering at acquisition.
- Meetings, races, runners and sequential 200m sectionals retain the required variables.
- API defaults to WA; optional Ascot/Saturday query filters.
- Class dropper: ClsDiff < 0 AND KRI >= 70. Elite closer: FML200 >= 2.5 AND RunStyle in Mid-Field, Backmarker.
- Trainer reports: RB == 0 and DLR <= 7; only settled starts count; flat-unit ROI uses recorded decimal starting prices and excludes unpriced starts from ROI denominators.
- Do not claim live provider validation or public deployment without credentials and destination. Synthetic data must be explicitly labelled.
- This project is published in the HorseRiding repository; the earlier development branch was named horse-riding.

## Review Focus
- Provider schema drift/missing IDs must preserve raw payload and report failure, never silently invent figures.
- Idempotent reruns and partial failures must not duplicate rows or erase source history.
- Missing results/prices, scratches, dead heats and zero denominators must not inflate trainer metrics.
- Auth bypass, injection, unsafe rendering, secrets in logs and TLS termination.
- Boundary values, WA scoping, pagination and stable sectional ordering.

## Shared Interfaces
- `punting.models`: Base; RawFeed; Meeting(id:str,date:date,track:str,state:str,rail_position,track_condition,moisture_variant,raw_payload); Race(id:str,meeting_id:str,number:int,distance:int,class_grade,class_par,raw_payload); Runner(id:str,race_id:str,horse_name,barrier,weight,trainer,jockey,prse,run_style,kri,cls_diff,fml600,fml200,rb,dlr,finish_position,starting_price,scratched,dead_heat_factor,raw_payload); Sectional200m(id:int,runner_id:str,sequence:int,distance:int,sectional_time,increment,raw_last600,raw_last200,fml600,fml200,raw_payload).
- All optional metrics nullable. Runner IDs represent a race entry, not a persistent horse ID.
- `punting.db.make_engine(url)` and `punting.db.create_schema(engine)` for tests; Alembic for production.
- `punting.ingest.ingest_payload(session, endpoint:str, payload:dict, raw_body:bytes|None=None)` archives before normalization, idempotently projects canonical envelope `{meetings:[{..., races:[{..., runners:[{..., sectionals:[...]}]}]}]}`. Provider adapter separate; canonical fixture is NOT claimed to be provider schema.
- `punting.config.Settings`: database_url, api_token (SecretStr), admin_username, admin_password (SecretStr), punting_form_api_key (SecretStr|None), provider_base_url, provider_config_path, log_level. No real secrets in Git.
- `punting.api.create_app(settings:Settings|None=None, engine=None)`; `punting.api:app` lazy runtime defaults. Bearer API auth and HTTP Basic HTML; public `/health/live`, DB readiness `/health/ready`.

### Task 1 / Milestone 1: Ingestion
- [x] Write failing tests for payload preservation, duplicate replay, drift, retries and authentication redaction.
- [x] Implement HTTP client, mapping adapter, ingestion and CLI. Endpoints/field maps configurable until confirmed against professional-tier contract.
- [x] Run tests, exercise raw ingestion on PostgreSQL, record evidence.

### Task 2 / Milestone 2: Schema
- [x] Write failing tests for foreign keys, uniqueness and sectional ordering.
- [x] Implement relational tables, raw archive, indexes and Alembic migration.
- [x] Validate upgrade on empty PostgreSQL and idempotent ingestion; inspect indexes.

### Task 3 / Milestone 3: Query API and UI
- [x] Write failing tests for exact threshold boundaries, geography, auth, validation, trainer calculations and escaping.
- [x] Implement modular query endpoints, responsive HTML and health checks.
- [x] Run unit/API tests and real HTTP requests against PostgreSQL.

### Task 4 / Milestone 4: Deployment and handover
- [x] Implement Docker image, Compose, HTTPS/auth configuration, daily cron, backup/restore guidance and README.
- [x] Run full tests, build package/image, start services, test authenticated UI/API and security failures.
- [x] Independent review, fix important findings, record milestone status and external blockers truthfully.
- [x] Commit source, source specification, docs, tests and verification report; push `horse-riding` and verify remote SHA.

## Execution record

Software tasks executed and locally verified; see `docs/validation.md` for evidence and the explicitly pending live-provider/public-deployment acceptance. I added a similar public dataset for independent historical-data validation; the pinned HK adapter and reconciliation were added. Review defects were reproduced and fixed. Live production acceptance remains dependent on subscriber access and a deployment destination.
