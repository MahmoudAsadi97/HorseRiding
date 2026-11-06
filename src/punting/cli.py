"""Warehouse maintenance and daily/backfill ingestion commands."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from punting.config import Settings
from punting.db import create_schema, make_engine
from punting.ingest import IngestionError, ingest_response
from punting.models import Meeting, Race
from punting.provider import ProviderClient, ProviderError, normalize_response


def ingest_date(session, client: ProviderClient, target_date: date):
    """Fetch every listed meeting, then configured meeting/race detail feeds.

    No geography or betting filters are applied. Previously discovered races on
    the date are also refreshed, so results can arrive after the original card.
    A failed endpoint does not prevent unrelated meetings from being captured.
    """
    summary = {"date": target_date.isoformat(), "succeeded": 0, "failed": 0, "archived": 0}
    endpoints = client.config["endpoints"]
    lists = [name for name, spec in endpoints.items() if spec["scope"] == "meeting-list"]
    if not lists:
        raise ProviderError("Daily ingestion requires a meeting-list endpoint")

    def retain(result, *, normalize=None):
        summary["archived"] += 1
        return ingest_response(
            session,
            result.endpoint,
            result.body,
            status_code=result.status_code,
            normalize=normalize,
            decode=lambda _: result.json(),
            source_context={
                "date": target_date.isoformat(),
                "response_headers": dict(result.response_headers),
                "complete": result.complete,
            },
        )

    def retain_prior(results):
        for prior in results:
            try:
                retain(prior)
            except IngestionError:
                pass  # Retry failures remain visible on their immutable archive rows.

    def process(name, variables, context=None):
        canonical = {}
        try:
            response = client.fetch(name, variables)
        except ProviderError as error:
            retain_prior(error.prior_responses)
            summary["failed"] += 1
            return canonical
        retain_prior(response.prior_responses)

        def normalize(payload):
            canonical.update(
                normalize_response(payload, endpoints[name], {**variables, **(context or {})})
            )
            return canonical

        try:
            retain(response, normalize=normalize)
            summary["succeeded"] += 1
        except IngestionError:
            summary["failed"] += 1
        return canonical

    meetings = {}

    def merge_meetings(envelope):
        for meeting in envelope.get("meetings", []):
            identifier = meeting.get("id")
            if identifier is None:
                continue
            identifier = str(identifier)
            current = meetings.setdefault(identifier, {"id": identifier, "races": {}})
            current.update({key: value for key, value in meeting.items() if key != "races"})
            for race in meeting.get("races", []):
                if race.get("id") is not None:
                    current["races"].setdefault(str(race["id"]), {}).update(race)

    for name in lists:
        merge_meetings(process(name, {"date": target_date.isoformat()}))
    # A stored card can be refreshed even if a provider's current listing omits it.
    for meeting in session.scalars(select(Meeting).where(Meeting.date == target_date)):
        current = meetings.setdefault(
            meeting.id,
            {
                "id": meeting.id,
                "date": meeting.date.isoformat(),
                "track": meeting.track,
                "state": meeting.state,
                "races": {},
            },
        )
        for race in session.scalars(select(Race).where(Race.meeting_id == meeting.id)):
            current["races"].setdefault(
                race.id, {"id": race.id, "number": race.number, "distance": race.distance}
            )
    for meeting_id in list(meetings):
        variables = {"date": target_date.isoformat(), "meeting_id": meeting_id}
        for name, spec in endpoints.items():
            if spec["scope"] == "meeting":
                context = {
                    "meeting": {
                        key: value for key, value in meetings[meeting_id].items() if key != "races"
                    }
                }
                merge_meetings(process(name, variables, context))
        for race_id, race in list(meetings[meeting_id]["races"].items()):
            context = {
                "meeting": {
                    key: value for key, value in meetings[meeting_id].items() if key != "races"
                },
                "race": race,
            }
            for name, spec in endpoints.items():
                if spec["scope"] == "race":
                    merge_meetings(process(name, {**variables, "race_id": race_id}, context))
    return summary


def _iso_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Date must be YYYY-MM-DD") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Raw racing warehouse ingestion and maintenance")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "init-db", help="Create local/test schema (production: alembic upgrade head)"
    )
    importer = commands.add_parser(
        "import-json", help="Import a canonical JSON envelope; demo data is synthetic"
    )
    importer.add_argument("path", type=Path)
    importer.add_argument("--endpoint", default="local-import")
    daily = commands.add_parser("ingest-date", help="Fetch all configured feeds for one UTC date")
    daily.add_argument("--date", type=_iso_date, default=datetime.now(UTC).date())
    historical = commands.add_parser(
        "ingest-range", help="Backfill or refresh results for an inclusive date range"
    )
    historical.add_argument("--start", type=_iso_date, required=True)
    historical.add_argument("--end", type=_iso_date, required=True)
    args = parser.parse_args(argv)
    if args.command == "ingest-range" and args.end < args.start:
        parser.error("--end must be on or after --start")
    engine = None
    try:
        settings = Settings()
        engine = make_engine(settings.database_url)
        if args.command == "init-db":
            create_schema(engine)
            print("Schema initialized. Production schema changes use Alembic.")
            return 0
        with Session(engine) as session:
            if args.command == "import-json":
                feed_id = ingest_response(
                    session,
                    args.endpoint,
                    args.path.read_bytes(),
                    source_context={"kind": "canonical-import", "filename": args.path.name},
                )
                print(json.dumps({"archived_feed_id": feed_id, "status": "succeeded"}))
                return 0
            config = json.loads(settings.provider_config_path.read_text())
            api_key = (
                settings.punting_form_api_key.get_secret_value()
                if settings.punting_form_api_key
                else None
            )
            with ProviderClient(settings.provider_base_url, api_key, config) as client:
                current = args.date if args.command == "ingest-date" else args.start
                end = current if args.command == "ingest-date" else args.end
                failures = 0
                while current <= end:
                    summary = ingest_date(session, client, current)
                    print(json.dumps(summary))
                    failures += summary["failed"]
                    current += timedelta(days=1)
                return 1 if failures else 0
    except (ProviderError, IngestionError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, SQLAlchemyError):
        # Configuration/DB exception strings may embed URLs or credentials.
        print(
            "Command failed: check database availability, input files and provider configuration.",
            file=sys.stderr,
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
