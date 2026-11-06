"""Provider contract tests use invented transport payloads, never a claimed live schema."""

from copy import deepcopy

import httpx
import pytest

from punting.provider import ProviderClient, ProviderError, normalize_response


def config(**overrides):
    value = {
        "auth": {"location": "query", "name": "apiKey"},
        "max_attempts": 3,
        "max_retry_delay": 10,
        "endpoints": {
            "meetings": {
                "scope": "meeting-list",
                "path": "/synthetic/meetings",
                "query": {"date": "{date}"},
                "mapping": {
                    "path": "data",
                    "fields": {
                        "id": "meetingId",
                        "date": "date",
                        "track": "track",
                        "state": "state",
                    },
                },
            }
        },
    }
    value.update(overrides)
    return value


def test_fetch_retains_exact_bytes_and_does_not_include_query_credentials_in_provenance():
    def serve(request):
        assert request.url.params["apiKey"] == "private-secret"
        assert request.url.params["date"] == "2026-11-05"
        return httpx.Response(
            200, content=b'{  "data" : [] }\n', headers={"Content-Type": "application/json"}
        )

    with ProviderClient(
        "https://api.example.test", "private-secret", config(), transport=httpx.MockTransport(serve)
    ) as client:
        result = client.fetch("meetings", {"date": "2026-11-05"})
    assert result.body == b'{  "data" : [] }\n'
    assert result.json() == {"data": []}
    assert result.status_code == 200
    assert "private-secret" not in repr(result)
    assert "apiKey" not in result.endpoint


def test_retries_transient_response_honors_retry_after_and_is_bounded():
    responses = iter([429, 503, 503])
    sleeps = []

    def serve(request):
        return httpx.Response(
            next(responses), content=b'{"error":"try again"}', headers={"Retry-After": "2"}
        )

    with ProviderClient(
        "https://api.example.test",
        "x",
        config(),
        transport=httpx.MockTransport(serve),
        sleep=sleeps.append,
    ) as client:
        result = client.fetch("meetings", {"date": "2026-11-05"})
    assert result.status_code == 503
    assert result.body == b'{"error":"try again"}'
    assert result.attempts == 3
    assert sleeps == [2.0, 2.0]


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 422])
def test_permanent_errors_and_redirects_are_returned_without_retry(status):
    calls = []

    def serve(request):
        calls.append(str(request.url))
        return httpx.Response(status, content=b"no", headers={"Location": "https://evil.test/"})

    with ProviderClient(
        "https://api.example.test",
        "x",
        config(),
        transport=httpx.MockTransport(serve),
        sleep=lambda _: pytest.fail("retried"),
    ) as client:
        result = client.fetch("meetings", {"date": "2026-11-05"})
    assert len(calls) == 1
    assert result.status_code == status


def test_invalid_json_is_returned_before_safe_decode_failure():
    with ProviderClient(
        "https://api.example.test",
        "x",
        config(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"bad json")),
    ) as client:
        result = client.fetch("meetings", {"date": "2026-11-05"})
    assert result.body == b"bad json"
    with pytest.raises(ProviderError, match="JSON"):
        result.json()


def test_network_failures_are_bounded_and_never_reveal_request_url_or_key():
    calls = []

    def serve(request):
        calls.append(1)
        raise httpx.ConnectError(
            "leaked https://api.example.test/?apiKey=private-secret", request=request
        )

    with (
        ProviderClient(
            "https://api.example.test",
            "private-secret",
            config(),
            transport=httpx.MockTransport(serve),
            sleep=lambda _: None,
        ) as client,
        pytest.raises(ProviderError) as caught,
    ):
        client.fetch("meetings", {"date": "2026-11-05"})
    assert len(calls) == 3
    assert "private-secret" not in str(caught.value)
    assert "https://" not in str(caught.value)


@pytest.mark.parametrize(
    "base",
    [
        "http://api.example.test",
        "https://user:secret@api.example.test",
        "https://api.example.test/?key=x",
    ],
)
def test_rejects_non_https_or_credential_bearing_base_urls(base):
    with pytest.raises(ProviderError):
        ProviderClient(base, "x", config())


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.test/leak",
        "//evil.test/leak",
        "/safe?apiKey=secret",
        "/safe#fragment",
        "/safe\\evil",
    ],
)
def test_endpoint_configuration_cannot_change_origin_or_smuggle_query(path):
    cfg = config()
    cfg["endpoints"]["meetings"]["path"] = path
    with pytest.raises(ProviderError):
        ProviderClient("https://api.example.test", "x", cfg)


def test_path_variables_are_encoded_and_header_auth_is_supported():
    cfg = config(auth={"location": "header", "name": "X-Api-Key"})
    cfg["endpoints"]["meetings"]["path"] = "/synthetic/{meeting_id}"
    cfg["endpoints"]["meetings"]["query"] = {}

    def serve(request):
        assert request.headers["X-Api-Key"] == "private-secret"
        assert request.url.host == "api.example.test"
        assert request.url.raw_path == b"/synthetic/a%2Fb%3Fx%3Dy"
        return httpx.Response(200, json={"data": []})

    with ProviderClient(
        "https://api.example.test", "private-secret", cfg, transport=httpx.MockTransport(serve)
    ) as client:
        result = client.fetch("meetings", {"meeting_id": "a/b?x=y"})
    assert result.status_code == 200


def test_mapping_preserves_full_source_rows_and_all_required_metrics():
    payload = {
        "data": [
            {
                "meetingId": "m1",
                "date": "2026-11-05",
                "track": "Ascot",
                "state": "WA",
                "extra": {"preserve": True},
                "races": [
                    {
                        "raceId": "r1",
                        "number": 1,
                        "runners": [
                            {
                                "entryId": "e1",
                                "KRI": 70,
                                "ClsDiff": -1,
                                "FML600": 3.1,
                                "FML200": 2.5,
                                "RB": 0,
                                "DLR": 7,
                                "RunStyle": "Backmarker",
                                "PrSe": 10,
                                "splits": [{"n": 1, "elapsed": 12.1, "untouched": "yes"}],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    original = deepcopy(payload)
    endpoint = config()["endpoints"]["meetings"]
    endpoint["mapping"]["children"] = {
        "races": {
            "path": "races",
            "fields": {"id": "raceId", "number": "number"},
            "children": {
                "runners": {
                    "path": "runners",
                    "fields": {
                        "id": "entryId",
                        "kri": "KRI",
                        "cls_diff": "ClsDiff",
                        "fml600": "FML600",
                        "fml200": "FML200",
                        "rb": "RB",
                        "dlr": "DLR",
                        "run_style": "RunStyle",
                        "prse": "PrSe",
                    },
                    "children": {
                        "sectionals": {
                            "path": "splits",
                            "fields": {"sequence": "n", "sectional_time": "elapsed"},
                        }
                    },
                }
            },
        }
    }
    result = normalize_response(payload, endpoint)
    meeting = result["meetings"][0]
    runner = meeting["races"][0]["runners"][0]
    assert (
        runner["kri"],
        runner["cls_diff"],
        runner["fml600"],
        runner["fml200"],
        runner["rb"],
        runner["dlr"],
    ) == (70, -1, 3.1, 2.5, 0, 7)
    assert runner["run_style"] == "Backmarker"
    assert runner["prse"] == 10
    assert runner["sectionals"][0]["sectional_time"] == 12.1
    assert meeting["raw_payload"] == original["data"][0]
    assert runner["raw_payload"] == original["data"][0]["races"][0]["runners"][0]
    assert payload == original


def test_race_detail_wraps_required_parent_context_and_preserves_missing_optional_fields():
    endpoint = {
        "scope": "race",
        "mapping": {"path": "item", "fields": {"id": "raceId", "number": "n", "class_par": "par"}},
    }
    parent = {"id": "m1", "date": "2026-11-05", "track": "Ascot", "state": "WA"}
    result = normalize_response({"item": {"raceId": "r1", "n": 1}}, endpoint, {"meeting": parent})
    assert result["meetings"][0]["id"] == "m1"
    assert result["meetings"][0]["races"][0]["id"] == "r1"
    assert "class_par" not in result["meetings"][0]["races"][0]
    assert "races" not in parent


@pytest.mark.parametrize(
    "payload", [{"renamed": []}, {"data": [{"date": "2026-11-05"}]}, {"data": "oops"}]
)
def test_schema_drift_is_explicit_failure(payload):
    with pytest.raises(ProviderError):
        normalize_response(payload, config()["endpoints"]["meetings"])


def test_compressed_response_archives_received_bytes_and_decodes_separately():
    import gzip

    body = gzip.compress(b'{"data": []}')
    with ProviderClient(
        "https://api.example.test",
        "x",
        config(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, stream=httpx.ByteStream(body), headers={"Content-Encoding": "gzip"}
            )
        ),
    ) as client:
        result = client.fetch("meetings", {"date": "2026-11-05"})
    assert result.body == body
    assert result.json() == {"data": []}


def test_successful_retry_retains_prior_response_for_archiving():
    responses = iter(
        [httpx.Response(503, content=b"first failure"), httpx.Response(200, content=b"{}")]
    )
    with ProviderClient(
        "https://api.example.test",
        "x",
        config(),
        transport=httpx.MockTransport(lambda _: next(responses)),
        sleep=lambda _: None,
    ) as client:
        result = client.fetch("meetings", {"date": "2026-11-05"})
    assert result.prior_responses[0].body == b"first failure"
    assert result.prior_responses[0].status_code == 503


def test_network_failure_after_response_exposes_prior_response_for_archiving():
    calls = []

    def serve(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, content=b"first failure")
        raise httpx.ConnectError("private-secret", request=request)

    with (
        ProviderClient(
            "https://api.example.test",
            "private-secret",
            config(),
            transport=httpx.MockTransport(serve),
            sleep=lambda _: None,
        ) as client,
        pytest.raises(ProviderError) as caught,
    ):
        client.fetch("meetings", {"date": "2026-11-05"})
    assert caught.value.prior_responses[0].body == b"first failure"


def test_httpx_info_logs_redact_query_auth(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="httpx")
    with ProviderClient(
        "https://api.example.test",
        "a private&secret",
        config(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"{}")),
    ) as client:
        client.fetch("meetings", {"date": "2026-11-05"})
    assert "a private&secret" not in caplog.text
    assert "a+private%26secret" not in caplog.text
    assert "a%20private%26secret" not in caplog.text


def test_missing_optional_children_are_omitted_and_context_seeds_meeting_detail():
    endpoint = {
        "scope": "meeting",
        "mapping": {
            "path": "item",
            "fields": {"id": "id", "track": "track"},
            "children": {"races": {"path": "races", "fields": {"id": "id"}}},
        },
    }
    result = normalize_response(
        {"item": {"track": None}},
        endpoint,
        {"meeting": {"id": "m1", "date": "2026-11-05", "track": "Ascot", "state": "WA"}},
    )
    assert result["meetings"][0]["track"] == "Ascot"
    assert "races" not in result["meetings"][0]


def test_context_selector_maps_requested_date_without_inventing_data():
    endpoint = config()["endpoints"]["meetings"]
    endpoint["mapping"]["fields"]["date"] = {"context": "date"}
    result = normalize_response(
        {"data": [{"meetingId": "m1", "track": "Ascot", "state": "WA"}]},
        endpoint,
        {"date": "2026-11-05"},
    )
    assert result["meetings"][0]["date"] == "2026-11-05"


def test_provenance_identifies_requested_date_without_authentication():
    with ProviderClient(
        "https://api.example.test",
        "private-secret",
        config(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"{}")),
    ) as client:
        result = client.fetch("meetings", {"date": "2026-11-05"})
    assert "date=2026-11-05" in result.endpoint
    assert "api.example.test" in result.endpoint
    assert "private-secret" not in result.endpoint


def test_truncated_responses_preserve_received_bytes_before_retry():
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"partial":'
            raise httpx.ReadError("private-secret")

    responses = iter(
        [httpx.Response(200, stream=BrokenStream()), httpx.Response(200, content=b"{}")]
    )
    with ProviderClient(
        "https://api.example.test",
        "private-secret",
        config(),
        transport=httpx.MockTransport(lambda _: next(responses)),
        sleep=lambda _: None,
    ) as client:
        result = client.fetch("meetings", {"date": "2026-11-05"})
    assert result.prior_responses[0].body == b'{"partial":'
    assert result.prior_responses[0].complete is False
    assert result.complete is True
