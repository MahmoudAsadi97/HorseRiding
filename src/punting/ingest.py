"""Archive first, then project canonical entities in an atomic transaction.

Ingestion owns its session's transaction: an archive commit precedes normalization,
so even failed normalization leaves the exact response available for investigation.
Use a dedicated session, never a session holding unrelated changes.
"""

import copy
import hashlib
import json
import math
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.exc import DataError

from punting.models import Meeting, Race, RawFeed, Runner, Sectional200m


class IngestionError(ValueError):
    """A source response was archived but could not be projected."""


def safe_endpoint(endpoint):
    parts = urlsplit(endpoint)
    query = [
        (
            key,
            "[redacted]"
            if any(word in key.lower() for word in ("key", "token", "secret", "auth", "password"))
            else value,
        )
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    return urlunsplit(
        (parts.scheme, host if parts.netloc else "", parts.path, urlencode(query), "")
    )


def _archive(session, endpoint, body, payload, status_code=200, source_context=None):
    incompatible = False
    try:
        # Python's JSON decoder permits NaN/Infinity; PostgreSQL JSONB does not.
        json.dumps(payload, allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError):
        incompatible = True
    metadata = {
        "endpoint": safe_endpoint(endpoint),
        "raw_body": body,
        "sha256": hashlib.sha256(body).hexdigest(),
        "response_status": status_code,
        "source_context": copy.deepcopy(source_context or {}),
    }
    error = "Source JSON cannot be represented in the archive JSON column; original bytes retained"
    feed = RawFeed(
        **metadata,
        payload=None if incompatible else copy.deepcopy(payload),
        normalization_status="failed" if incompatible else "pending",
        normalization_error=error if incompatible else None,
    )
    session.add(feed)
    try:
        session.commit()
    except DataError:
        # JSONB additionally rejects NUL characters, invalid surrogates and some
        # numeric magnitudes. A failed INSERT is rolled back before retaining
        # the byte-exact response with an explicit unsupported-JSON outcome.
        session.rollback()
        feed = RawFeed(
            **metadata, payload=None, normalization_status="failed", normalization_error=error
        )
        session.add(feed)
        session.commit()
    return feed.id


def archive_source(session, endpoint: str, raw_body: bytes, *, payload=None, source_context=None):
    """Retain source evidence without claiming that it has been projected."""
    feed_id = _archive(session, endpoint, raw_body, payload, source_context=source_context)
    if session.get(RawFeed, feed_id).normalization_status != "failed":
        _record_outcome(session, feed_id, "archived")
    return feed_id


def _record_outcome(session, feed_id, status, error=None):
    feed = session.get(RawFeed, feed_id)
    feed.normalization_status = status
    feed.normalization_error = error
    session.commit()


def ingest_payload(session, endpoint: str, payload: dict, raw_body: bytes | None = None):
    """Import a canonical envelope, retaining every source payload and fetch event."""
    body = raw_body if raw_body is not None else json.dumps(payload, ensure_ascii=True).encode()
    feed_id = _archive(session, endpoint, body, payload)
    return _normalize_and_project(session, feed_id, lambda: payload)


def ingest_response(
    session,
    endpoint: str,
    raw_body: bytes,
    *,
    status_code=200,
    normalize=None,
    source_context=None,
    decode=None,
):
    """Retain HTTP bytes despite decoder errors, before mapping or status checks."""
    try:
        payload = decode(raw_body) if decode else json.loads(raw_body)
        json_error = False
    except Exception:  # noqa: BLE001 - a decoder failure must never discard response evidence
        payload, json_error = None, True
    feed_id = _archive(session, endpoint, raw_body, payload, status_code, source_context)

    def canonical():
        if not 200 <= status_code < 300:
            raise IngestionError(f"Provider returned HTTP {status_code}; response archived")
        if json_error:
            raise IngestionError("Response is not valid JSON; response archived")
        return normalize(payload) if normalize else payload

    return _normalize_and_project(session, feed_id, canonical)


def _normalize_and_project(session, feed_id, canonical):
    try:
        archived = session.get(RawFeed, feed_id)
        if archived.normalization_status == "failed":
            raise IngestionError(archived.normalization_error)
        payload = canonical()
        _project(session, payload, feed_id)
        session.flush()
        _record_outcome(session, feed_id, "succeeded")
        return feed_id
    except Exception as error:  # noqa: BLE001 - archive any normalization failure without leaking source values
        session.rollback()
        # Never persist HTTP exception strings or source values: they can contain secrets.
        message = (
            str(error)
            if isinstance(error, IngestionError)
            else f"Normalization failed ({type(error).__name__}); inspect archived source and mapping"
        )
        _record_outcome(session, feed_id, "failed", message)
        raise IngestionError(message) from None


def _rows(node, key, required=False):
    if key not in node:
        if required:
            raise IngestionError(f"Missing required {key} array")
        return []
    rows = node[key]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise IngestionError(f"{key} must be an array of objects")
    return rows


def _identifier(row, field="id"):
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip():
        raise IngestionError(f"Missing or invalid {field}")
    value = str(value)
    if len(value) > 200:
        raise IngestionError(f"{field} exceeds 200 characters")
    return value


def _number(value, field, integer=False):
    if value is None:
        return None
    try:
        number = float(value)
        if (
            isinstance(value, bool)
            or not math.isfinite(number)
            or (integer and not number.is_integer())
        ):
            raise ValueError
        return int(number) if integer else number
    except (ValueError, TypeError, OverflowError):
        raise IngestionError(f"Invalid numeric field {field}") from None


INTEGER_FIELDS = {"number", "distance", "barrier", "rb", "dlr", "finish_position", "sequence"}
FLOAT_FIELDS = {
    "moisture_variant",
    "class_par",
    "weight",
    "prse",
    "kri",
    "cls_diff",
    "fml600",
    "fml200",
    "starting_price",
    "settled_win_return",
    "dead_heat_factor",
    "sectional_time",
    "increment",
    "raw_last600",
    "raw_last200",
}


def _value(field, value):
    if field in INTEGER_FIELDS | FLOAT_FIELDS:
        return _number(value, field, field in INTEGER_FIELDS)
    if field == "date":
        try:
            return value if isinstance(value, date) else date.fromisoformat(value)
        except (ValueError, TypeError):
            raise IngestionError("Invalid date; expected YYYY-MM-DD") from None
    if field == "scratched":
        if isinstance(value, bool):
            return value
        raise IngestionError("scratched must be a boolean")
    if value is not None and not isinstance(value, str):
        raise IngestionError(f"{field} must be text or null")
    if field == "result_status" and value not in (
        None,
        "pending",
        "finished",
        "dnf",
        "disqualified",
        "void",
    ):
        raise IngestionError("Invalid result_status")
    return value


def _upsert(session, model, row, feed_id, *, parent=None):
    identifier = _identifier(row)
    instance = session.get(model, identifier)
    is_new = instance is None
    if is_new:
        instance = model(id=identifier)
        session.add(instance)
    if parent:
        key, parent_id = parent
        if not is_new and getattr(instance, key) != parent_id:
            raise IngestionError("Entity parent reassignment is not permitted")
        setattr(instance, key, parent_id)
    required = {
        Meeting: ("date", "track", "state"),
        Race: ("number", "distance"),
        Runner: ("horse_name",),
    }[model]
    for field in required:
        if (is_new and field not in row) or (field in row and row[field] in (None, "")):
            raise IngestionError(f"Missing required {model.__tablename__}.{field}")
    allowed = set(model.__table__.columns.keys()) - {
        "id",
        "meeting_id",
        "race_id",
        "raw_payload",
        "source_feed_id",
    }
    for field in allowed & row.keys():
        setattr(instance, field, _value(field, row[field]))
    instance.raw_payload = copy.deepcopy(row.get("raw_payload", row))
    instance.source_feed_id = feed_id
    session.flush()
    return instance


def _sectional(session, runner_id, row, feed_id):
    sequence = _number(row.get("sequence"), "sequence", True)
    if sequence is None or sequence <= 0:
        raise IngestionError("Missing or invalid sectional sequence")
    instance = session.scalar(
        select(Sectional200m).where(
            Sectional200m.runner_id == runner_id, Sectional200m.sequence == sequence
        )
    )
    if instance is None:
        if row.get("distance") is None:
            raise IngestionError("Missing sectional distance")
        instance = Sectional200m(runner_id=runner_id, sequence=sequence)
        session.add(instance)
    allowed = set(Sectional200m.__table__.columns.keys()) - {
        "id",
        "runner_id",
        "raw_payload",
        "source_feed_id",
    }
    for field in allowed & row.keys():
        setattr(instance, field, _value(field, row[field]))
    instance.raw_payload = copy.deepcopy(row.get("raw_payload", row))
    instance.source_feed_id = feed_id
    session.flush()


def _project(session, payload, feed_id):
    if not isinstance(payload, dict):
        raise IngestionError("Canonical payload must be an object containing meetings")
    for meeting_row in _rows(payload, "meetings", required=True):
        meeting = _upsert(session, Meeting, meeting_row, feed_id)
        for race_row in _rows(meeting_row, "races"):
            race = _upsert(session, Race, race_row, feed_id, parent=("meeting_id", meeting.id))
            for runner_row in _rows(race_row, "runners"):
                runner = _upsert(session, Runner, runner_row, feed_id, parent=("race_id", race.id))
                seen = set()
                for sectional in _rows(runner_row, "sectionals"):
                    sequence = _number(sectional.get("sequence"), "sequence", True)
                    if sequence in seen:
                        raise IngestionError("Duplicate sectional sequence in source")
                    seen.add(sequence)
                    _sectional(session, runner.id, sectional, feed_id)
