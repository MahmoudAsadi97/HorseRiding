"""Configurable provider adapter; no live Punting Form response schema is assumed.

A reviewed endpoint mapping, obtained from the subscriber's current API contract,
converts source rows into the warehouse's canonical envelope. Fetching and decoding
are separate so every received byte can be archived before decoding or mapping.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import re
import time
import zlib
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import MappingProxyType
from typing import Any, Self
from urllib.parse import quote, quote_plus, urlencode, urlsplit

import httpx


class ProviderError(ValueError):
    """A provider/configuration failure whose message is safe to log."""

    def __init__(self, message: str, prior_responses: tuple[FetchResult, ...] = ()):
        super().__init__(message)
        self.prior_responses = prior_responses


@dataclass(frozen=True)
class FetchResult:
    endpoint: str
    body: bytes = field(repr=False)
    status_code: int
    response_headers: Mapping[str, str]
    attempts: int = 1
    prior_responses: tuple[FetchResult, ...] = field(default=(), repr=False)
    complete: bool = True

    def json(self) -> Any:
        if not self.complete:
            raise ProviderError("Provider response body was interrupted")
        try:
            body = self.body
            encoding = self.response_headers.get("content-encoding", "identity").lower()
            if encoding == "gzip":
                body = gzip.decompress(body)
            elif encoding == "deflate":
                body = zlib.decompress(body)
            elif encoding != "identity":
                raise ProviderError("Provider response uses an unsupported content encoding")
            return json.loads(body)
        except (ValueError, UnicodeError, OSError, zlib.error):
            raise ProviderError("Provider response is not valid JSON") from None


class _SecretRedactor(logging.Filter):
    """httpx logs request URLs at INFO; redact credentials before handlers see them."""

    def __init__(self, secret: str):
        super().__init__()
        self.values = (secret, quote(secret, safe=""), quote_plus(secret))

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for value in self.values:
            message = message.replace(value, "[REDACTED]")
        record.msg, record.args = message, ()
        return True


class ProviderClient:
    """Bounded synchronous GET client restricted to one configured HTTPS origin."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        config: dict[str, Any],
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        parts = urlsplit(base_url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in ("", "/")
        ):
            raise ProviderError("Provider base URL must be a credential-free HTTPS origin")
        self.base_url = base_url.rstrip("/")
        self.config = deepcopy(config)
        self._api_key = api_key
        self._sleep = sleep
        self.max_attempts = config.get("max_attempts", 3)
        self.max_retry_delay = config.get("max_retry_delay", 30)
        if (
            type(self.max_attempts) is not int
            or not 1 <= self.max_attempts <= 10
            or not isinstance(self.max_retry_delay, (int, float))
            or not math.isfinite(self.max_retry_delay)
            or not 0 <= self.max_retry_delay <= 300
        ):
            raise ProviderError("Retry limits must be finite and bounded")
        endpoints = self.config.get("endpoints")
        if not isinstance(endpoints, dict) or not endpoints:
            raise ProviderError("Provider configuration requires endpoints")
        for name, endpoint in endpoints.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise ProviderError(
                    "Endpoint names must contain only letters, digits, underscores or hyphens"
                )
            path = endpoint.get("path", "") if isinstance(endpoint, dict) else ""
            self._validate_path(path)
            if endpoint.get("scope") not in ("meeting-list", "meeting", "race"):
                raise ProviderError("Endpoint scope must be meeting-list, meeting, or race")
        auth = config.get("auth", {"location": "header", "name": "X-Api-Key"})
        if not isinstance(auth, dict) or auth.get("location") not in ("header", "query", "none"):
            raise ProviderError("Authentication location must be header, query, or none")
        if auth["location"] != "none" and (
            not isinstance(auth.get("name"), str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", auth["name"])
        ):
            raise ProviderError("Authentication parameter name is invalid")
        if auth["location"] == "header" and auth["name"].lower() in (
            "host",
            "content-length",
            "connection",
        ):
            raise ProviderError("Authentication header cannot alter routing or framing")
        self._auth = auth
        timeout = config.get("timeout", 30)
        if (
            not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 300
        ):
            raise ProviderError("Provider timeout must be between zero and 300 seconds")
        self._client = httpx.Client(timeout=timeout, transport=transport, follow_redirects=False)
        self._log_filters = []
        if api_key:
            # Filters are installed on the actual emitting loggers, before any
            # application handler can format a credential-bearing URL or header.
            redactor = _SecretRedactor(api_key)
            for logger_name in (
                "httpx",
                "httpcore.connection",
                "httpcore.http11",
                "httpcore.http2",
                "httpcore.proxy",
                "httpcore.socks",
            ):
                logger = logging.getLogger(logger_name)
                logger.addFilter(redactor)
                self._log_filters.append((logger, redactor))

    @staticmethod
    def _validate_path(path: Any) -> None:
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path.startswith("//")
            or any(character in path for character in ("?", "#", "\\", "\r", "\n"))
        ):
            raise ProviderError("Endpoint paths must be absolute paths on the configured origin")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()
        for logger, redactor in self._log_filters:
            logger.removeFilter(redactor)
        self._log_filters.clear()

    def _delay(self, response: httpx.Response | None, attempt: int) -> float:
        delay = min(2 ** (attempt - 1), self.max_retry_delay)
        value = response.headers.get("Retry-After") if response is not None else None
        if value:
            try:
                parsed = float(value)
            except ValueError:
                try:
                    stamp = parsedate_to_datetime(value)
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=UTC)
                    parsed = (stamp - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    return float(delay)
            if math.isfinite(parsed):
                delay = min(max(parsed, 0), self.max_retry_delay)
        return float(delay)

    def fetch(self, endpoint_name: str, variables: dict[str, Any] | None = None) -> FetchResult:
        endpoint = self.config["endpoints"].get(endpoint_name)
        if endpoint is None:
            raise ProviderError("Unknown provider endpoint")
        values = {key: str(value) for key, value in (variables or {}).items()}
        try:
            path = endpoint["path"].format_map(
                {key: quote(value, safe="") for key, value in values.items()}
            )
            params = {
                key: str(value).format_map(values)
                for key, value in endpoint.get("query", {}).items()
            }
        except (KeyError, ValueError, AttributeError):
            raise ProviderError("Endpoint template has missing or invalid variables") from None
        self._validate_path(path)
        provenance_params = {
            key: value for key, value in params.items() if key != self._auth.get("name")
        }
        provenance = endpoint_name + ":" + self.base_url + path
        if provenance_params:
            provenance += "?" + urlencode(provenance_params)
        if self._api_key:
            for secret_value in (
                self._api_key,
                quote(self._api_key, safe=""),
                quote_plus(self._api_key),
            ):
                provenance = provenance.replace(secret_value, "[REDACTED]")
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if self._auth["location"] != "none":
            if not self._api_key:
                raise ProviderError("Provider API key is required by configured authentication")
            if self._auth["location"] == "header":
                headers[self._auth["name"]] = self._api_key
            else:
                params[self._auth["name"]] = self._api_key
        previous: list[FetchResult] = []
        transient = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
        for attempt in range(1, self.max_attempts + 1):
            response = None
            chunks: list[bytes] = []
            retained_headers = MappingProxyType({})
            try:
                request = self._client.build_request(
                    "GET", self.base_url + path, params=params, headers=headers
                )
                response = self._client.send(request, stream=True)
                retained_headers = MappingProxyType(
                    {
                        key: value
                        for key, value in response.headers.items()
                        if key
                        in (
                            "content-type",
                            "content-encoding",
                            "date",
                            "etag",
                            "last-modified",
                            "x-request-id",
                        )
                    }
                )
                try:
                    if response.is_stream_consumed:
                        chunks.append(response.content)
                    else:
                        for chunk in response.iter_raw():
                            chunks.append(chunk)  # noqa: PERF402 - retain partial bytes on stream failure
                    body = b"".join(chunks)
                finally:
                    response.close()
            except transient:
                if response is not None:
                    previous.append(
                        FetchResult(
                            provenance,
                            b"".join(chunks),
                            response.status_code,
                            retained_headers,
                            attempt,
                            complete=False,
                        )
                    )
                if attempt == self.max_attempts:
                    raise ProviderError(
                        "Provider request failed after bounded network retries", tuple(previous)
                    ) from None
            except httpx.RequestError:
                raise ProviderError("Provider request failed", tuple(previous)) from None
            else:
                result = FetchResult(
                    provenance,
                    body,
                    response.status_code,
                    retained_headers,
                    attempt,
                    tuple(previous),
                )
                if (
                    response.status_code not in (408, 425, 429, 500, 502, 503, 504)
                    or attempt == self.max_attempts
                ):
                    return result
                previous.append(result)
            self._sleep(self._delay(response, attempt))
        raise ProviderError("Provider request did not complete")


_MISSING = object()
_ENTITY_FIELDS = {
    "meetings": {
        "id",
        "date",
        "track",
        "state",
        "rail_position",
        "track_condition",
        "moisture_variant",
    },
    "races": {"id", "meeting_id", "number", "distance", "class_grade", "class_par"},
    "runners": {
        "id",
        "race_id",
        "horse_name",
        "barrier",
        "weight",
        "trainer",
        "jockey",
        "prse",
        "run_style",
        "kri",
        "cls_diff",
        "fml600",
        "fml200",
        "rb",
        "dlr",
        "finish_position",
        "starting_price",
        "scratched",
        "dead_heat_factor",
        "result_status",
    },
    "sectionals": {
        "sequence",
        "distance",
        "sectional_time",
        "increment",
        "raw_last600",
        "raw_last200",
        "fml600",
        "fml200",
    },
}
_CHILDREN = {"meetings": "races", "races": "runners", "runners": "sectionals"}


def _lookup(value: Any, path: str) -> Any:
    if path in ("", ".", "$"):
        return value
    if not isinstance(path, str):
        raise ProviderError("Mapping paths must be strings")
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _map_rows(
    source: Any,
    node: dict[str, Any],
    entity: str,
    context: dict[str, Any],
    *,
    optional: bool = False,
) -> list[dict[str, Any]]:
    rows = _lookup(source, node.get("path", ""))
    if rows is _MISSING or rows is None:
        if optional:
            return []
        raise ProviderError("Configured collection is absent from provider response")
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise ProviderError("Configured collection must contain objects")
    mapped = []
    for row in rows:
        if not isinstance(row, dict):
            raise ProviderError("Provider collection contains a non-object row")
        result = {}
        fields = node.get("fields", {})
        if not isinstance(fields, dict) or not fields:
            raise ProviderError("Every mapping node requires explicit field mappings")
        for field_name, source_path in fields.items():
            if field_name not in _ENTITY_FIELDS[entity]:
                raise ProviderError("Mapping contains an unknown canonical field")
            if isinstance(source_path, dict) and set(source_path) == {"context"}:
                value = _lookup(context, source_path["context"])
            elif isinstance(source_path, str):
                value = _lookup(row, source_path)
            else:
                raise ProviderError("Field mappings must be source paths or context selectors")
            if value is not _MISSING:
                result[field_name] = deepcopy(value)
        identifier = "sequence" if entity == "sectionals" else "id"
        if result.get(identifier) in (None, ""):
            if entity == "meetings" and context.get("meeting", {}).get("id"):
                result["id"] = context["meeting"]["id"]
            elif entity == "races" and context.get("race", {}).get("id"):
                result["id"] = context["race"]["id"]
            else:
                raise ProviderError("Provider row is missing its configured stable identifier")
        result["raw_payload"] = deepcopy(row)
        for child_name, child_node in node.get("children", {}).items():
            if _CHILDREN.get(entity) != child_name:
                raise ProviderError("Mapping contains an invalid child collection")
            children = _map_rows(
                row, child_node, child_name, context, optional=child_node.get("optional", True)
            )
            # Absent optional data must not erase a prior projection.
            if children or _lookup(row, child_node.get("path", "")) not in (_MISSING, None):
                result[child_name] = children
        mapped.append(result)
    return mapped


def normalize_response(
    payload: Any, endpoint_config: dict[str, Any], context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Convert explicit configured source paths into the canonical envelope.

    Raw source rows are copied unchanged into ``raw_payload``. Missing optional
    fields are omitted; unmapped required IDs or missing root collections fail.
    ``context`` supplies already discovered meeting/race parents to detail feeds.
    """
    context = context or {}
    scope = endpoint_config.get("scope")
    mapping = endpoint_config.get("mapping")
    if scope not in ("meeting-list", "meeting", "race") or not isinstance(mapping, dict):
        raise ProviderError("Endpoint needs a supported scope and explicit mapping")
    entity = "races" if scope == "race" else "meetings"
    rows = _map_rows(payload, mapping, entity, context)
    if scope == "race":
        parent = deepcopy(context.get("meeting"))
        if not isinstance(parent, dict) or not parent.get("id"):
            raise ProviderError("Race detail mapping requires its meeting context")
        race_context = context.get("race", {})
        rows = [
            {
                **deepcopy(race_context),
                **{key: value for key, value in row.items() if value is not None},
            }
            for row in rows
        ]
        parent["races"] = rows
        return {"meetings": [parent]}
    if scope == "meeting":
        parent = context.get("meeting", {})
        rows = [
            {**deepcopy(parent), **{key: value for key, value in row.items() if value is not None}}
            for row in rows
        ]
    return {"meetings": rows}
