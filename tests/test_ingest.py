import copy
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from punting.db import create_schema, make_engine
from punting.ingest import IngestionError, ingest_payload, ingest_response
from punting.models import Meeting, Race, RawFeed, Runner, Sectional200m


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    create_schema(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def payload():
    return {
        "meetings": [
            {
                "id": "m1",
                "date": "2026-10-31",
                "track": "Ascot",
                "state": "WA",
                "future_field": {"untouched": True},
                "races": [
                    {
                        "id": "r1",
                        "number": 1,
                        "distance": 1200,
                        "runners": [
                            {
                                "id": "r1:h1",
                                "horse_name": "Synthetic Runner",
                                "kri": 70,
                                "cls_diff": -1,
                                "fml200": 2.5,
                                "run_style": "Backmarker",
                                "rb": 0,
                                "dlr": 7,
                                "sectionals": [
                                    {"sequence": 2, "distance": 400, "sectional_time": 11.2},
                                    {"sequence": 1, "distance": 200, "sectional_time": 12.1},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }


def test_replay_retains_raw_history_but_does_not_duplicate_entities(session, payload):
    body = json.dumps(payload, indent=3).encode()
    ingest_payload(session, "fixture", payload, raw_body=body)
    ingest_payload(session, "fixture", payload, raw_body=body)
    assert session.scalar(select(func.count()).select_from(RawFeed)) == 2
    assert session.scalar(select(func.count()).select_from(Meeting)) == 1
    assert session.scalar(select(func.count()).select_from(Runner)) == 1
    assert session.scalar(select(func.count()).select_from(Sectional200m)) == 2
    feed = session.scalars(select(RawFeed)).first()
    assert feed.raw_body == body
    assert feed.sha256 == hashlib.sha256(body).hexdigest()
    assert feed.payload == payload
    assert session.get(Meeting, "m1").raw_payload["future_field"] == {"untouched": True}
    assert session.get(Meeting, "m1").date == date(2026, 10, 31)


def test_failed_normalization_retains_source_and_rolls_back_projection(session, payload):
    payload["meetings"][0]["races"][0]["runners"].append({"horse_name": "No ID"})
    with pytest.raises(IngestionError, match="id"):
        ingest_payload(session, "broken", payload)
    assert session.scalar(select(func.count()).select_from(Meeting)) == 0
    feed = session.scalars(select(RawFeed)).one()
    assert feed.payload == payload
    assert feed.normalization_status == "failed"
    assert "id" in feed.normalization_error


def test_partial_refresh_preserves_metrics_and_sectional_history(session, payload):
    ingest_payload(session, "initial", payload)
    patch = {
        "meetings": [
            {
                "id": "m1",
                "races": [
                    {
                        "id": "r1",
                        "runners": [
                            {
                                "id": "r1:h1",
                                "finish_position": 1,
                                "starting_price": 4.0,
                                "result_status": "finished",
                                "sectionals": [
                                    {"sequence": 2, "distance": 400, "sectional_time": 11.1}
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    ingest_payload(session, "result", patch)
    runner = session.get(Runner, "r1:h1")
    assert runner.kri == 70
    assert runner.finish_position == 1
    assert runner.result_status == "finished"
    assert session.scalar(select(func.count()).select_from(Sectional200m)) == 2
    assert (
        session.scalars(select(Sectional200m).order_by(Sectional200m.sequence))
        .first()
        .sectional_time
        == 12.1
    )
    assert session.scalar(select(func.count()).select_from(RawFeed)) == 2


def test_projection_keeps_actual_provider_row_as_raw_payload(session, payload):
    source = {"ProviderID": "m1", "unmapped": ["all", "fields"]}
    payload["meetings"][0]["raw_payload"] = source
    ingest_payload(session, "mapped", payload)
    assert session.get(Meeting, "m1").raw_payload == source


def test_missing_envelope_and_nonfinite_numbers_fail_loudly(session, payload):
    with pytest.raises(IngestionError, match="meetings"):
        ingest_payload(session, "drift", {"newEnvelope": []})
    payload["meetings"][0]["races"][0]["runners"][0]["kri"] = "NaN"
    with pytest.raises(IngestionError, match="kri"):
        ingest_payload(session, "bad-number", payload)
    assert session.scalar(select(func.count()).select_from(RawFeed)) == 2
    assert session.scalar(select(func.count()).select_from(Meeting)) == 0


def test_http_and_invalid_json_responses_are_archived_before_errors(session):
    with pytest.raises(IngestionError, match="HTTP 401"):
        ingest_response(
            session,
            "https://example.test/feed?apikey=do-not-store",
            b'{"error":"unauthorized"}',
            status_code=401,
        )
    with pytest.raises(IngestionError, match="JSON"):
        ingest_response(session, "/broken", b"not json")
    feeds = session.scalars(select(RawFeed).order_by(RawFeed.id)).all()
    assert len(feeds) == 2
    assert feeds[0].payload == {"error": "unauthorized"}
    assert "do-not-store" not in feeds[0].endpoint
    assert feeds[1].raw_body == b"not json"
    assert feeds[1].payload is None


def test_archive_content_cannot_be_updated_or_deleted(session, payload):
    ingest_payload(session, "source", payload)
    with pytest.raises(DatabaseError):
        session.execute(update(RawFeed).values(raw_body=b"tampered"))
        session.commit()
    session.rollback()
    feed = session.scalars(select(RawFeed)).one()
    with pytest.raises(DatabaseError):
        session.delete(feed)
        session.commit()
    session.rollback()
    assert session.scalar(select(func.count()).select_from(RawFeed)) == 1


def test_parent_id_reassignment_is_rejected_and_history_preserved(session, payload):
    ingest_payload(session, "first", payload)
    patch = copy.deepcopy(payload)
    patch["meetings"][0]["id"] = "different-meeting"
    with pytest.raises(IngestionError, match="parent"):
        ingest_payload(session, "second", patch)
    assert session.get(Race, "r1").meeting_id == "m1"
    assert session.get(Meeting, "different-meeting") is None


def test_compressed_response_archives_original_bytes_and_decoded_json(session, payload):
    import gzip

    body = gzip.compress(json.dumps(payload).encode())
    ingest_response(
        session, "compressed", body, decode=lambda value: json.loads(gzip.decompress(value))
    )
    feed = session.scalars(select(RawFeed)).one()
    assert feed.raw_body == body
    assert feed.payload == payload
    assert session.get(Runner, "r1:h1").kri == 70


def test_daily_ingestion_fetches_all_states_details_and_archives_retries(session):
    import httpx

    from punting.cli import ingest_date
    from punting.provider import ProviderClient

    calls = []
    config = {
        "auth": {"location": "none"},
        "max_attempts": 2,
        "endpoints": {
            "list": {
                "scope": "meeting-list",
                "path": "/meetings",
                "query": {"date": "{date}"},
                "mapping": {
                    "path": "meetings",
                    "fields": {
                        "id": "id",
                        "date": {"context": "date"},
                        "track": "track",
                        "state": "state",
                    },
                },
            },
            "detail": {
                "scope": "meeting",
                "path": "/meetings/{meeting_id}",
                "mapping": {
                    "path": "",
                    "fields": {"id": "id"},
                    "children": {
                        "races": {
                            "path": "races",
                            "fields": {"id": "id", "number": "number", "distance": "distance"},
                        }
                    },
                },
            },
            "results": {
                "scope": "race",
                "path": "/races/{race_id}",
                "mapping": {
                    "path": "",
                    "fields": {"id": "id"},
                    "children": {
                        "runners": {
                            "path": "runners",
                            "fields": {
                                "id": "id",
                                "horse_name": "horse_name",
                                "finish_position": "finish_position",
                            },
                        }
                    },
                },
            },
        },
    }

    def respond(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(503, json={"temporary": True}, headers={"Retry-After": "0"})
        if request.url.path == "/meetings":
            assert request.url.params["date"] == "2026-10-31"
            return httpx.Response(
                200,
                json={
                    "meetings": [
                        {"id": "wa", "date": "2026-10-31", "track": "Ascot", "state": "WA"},
                        {"id": "vic", "date": "2026-10-31", "track": "Flemington", "state": "VIC"},
                    ]
                },
            )
        if request.url.path.startswith("/meetings/"):
            meeting_id = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(
                200,
                json={
                    "id": meeting_id,
                    "races": [{"id": meeting_id + "-r", "number": 1, "distance": 1200}],
                },
            )
        race_id = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(
            200,
            json={
                "id": race_id,
                "runners": [{"id": race_id + "-h", "horse_name": "Demo", "finish_position": 1}],
            },
        )

    with ProviderClient(
        "https://example.test",
        None,
        config,
        transport=httpx.MockTransport(respond),
        sleep=lambda _: None,
    ) as client:
        result = ingest_date(session, client, date(2026, 10, 31))
    assert result["succeeded"] == 5
    assert result["failed"] == 0
    assert result["archived"] == 6
    assert session.get(Runner, "vic-r-h").finish_position == 1
    assert session.scalar(select(func.count()).select_from(RawFeed)) == 6


def test_cli_import_and_date_validation(tmp_path, monkeypatch, capsys):
    from punting.cli import main

    db_url = f"sqlite:///{tmp_path}/cli.db"
    monkeypatch.setenv("DATABASE_URL", db_url)
    assert main(["init-db"]) == 0
    assert main(["import-json", str(Path(__file__).parent / "fixtures/demo.json")]) == 0
    engine = make_engine(db_url)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Runner)) == 5
    with pytest.raises(SystemExit) as error:
        main(["ingest-date", "--date", "2026-13-01"])
    assert error.value.code == 2


def test_evidence_only_archive_retains_csv_without_claiming_projection(session):
    from punting.ingest import archive_source

    feed_id = archive_source(
        session, "public:source.csv", b"id,name\n1,Example\n", source_context={"license": "CC0"}
    )
    feed = session.get(RawFeed, feed_id)
    assert feed.raw_body == b"id,name\n1,Example\n"
    assert feed.source_context == {"license": "CC0"}
    assert feed.normalization_status == "archived"
    assert feed.payload is None


def test_settled_return_retains_source_payout_and_rejects_negative_values(session, payload):
    row = payload["meetings"][0]["races"][0]["runners"][0]
    row["settled_win_return"] = 3.45
    ingest_payload(session, "payout", payload)
    assert session.get(Runner, "r1:h1").settled_win_return == 3.45
    row["settled_win_return"] = -1
    with pytest.raises(IngestionError):
        ingest_payload(session, "bad-payout", payload)
    assert session.get(Runner, "r1:h1").settled_win_return == 3.45


@pytest.mark.parametrize("failure", [EOFError("truncated gzip"), RuntimeError("secret-token")])
def test_decoder_failure_still_retains_response_bytes(session, failure):
    def decode(_):
        raise failure

    with pytest.raises(IngestionError, match="JSON"):
        ingest_response(session, "broken-decoder", b"partial source bytes", decode=decode)
    feed = session.scalars(select(RawFeed)).one()
    assert feed.raw_body == b"partial source bytes"
    assert feed.normalization_status == "failed"
    assert "secret-token" not in feed.normalization_error
