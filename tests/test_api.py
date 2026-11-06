"""Authentication, validation, escaping and read-only API smoke tests."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

TOKEN = "test-api-token-that-is-longer-than-32-characters"
PASSWORD = "test-admin-password-long-enough"


@pytest.fixture
def api_client():
    from punting.api import create_app
    from punting.config import Settings
    from punting.models import Base, Meeting, Race, Runner

    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Meeting(id="meeting", date=date(2026, 10, 31), track="Ascot",
                            state="WA", raw_payload={}))
        session.add(Race(id="race", meeting_id="meeting", number=1, distance=1200,
                         raw_payload={}))
        session.flush()
        session.add(Runner(id="runner", race_id="race", horse_name='<script>alert(1)</script>',
                           trainer="Example", cls_diff=-1, kri=70, fml200=2.5,
                           run_style="Mid-Field", scratched=False, raw_payload={}))
        session.commit()
    settings = Settings(api_token=TOKEN, admin_username="owner", admin_password=PASSWORD)
    with TestClient(create_app(settings=settings, engine=engine)) as client:
        yield client
    engine.dispose()


@pytest.mark.parametrize("path", ["/api/class-droppers", "/api/elite-closers",
    "/api/trainers/first-up", "/api/trainers/backup", "/api/runners/runner/sectionals",
    "/api/meetings"])
def test_api_requires_bearer_and_all_routes_work(api_client, path):
    assert api_client.get(path).status_code == 401
    assert api_client.get(path, auth=("owner", PASSWORD)).status_code == 401
    assert api_client.get(path, headers={"Authorization": "Bearer incorrect"}).status_code == 401
    response = api_client.get(path, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert "items" in response.json()
    assert "total" in response.json()


def test_html_basic_auth_escapes_values_and_never_embeds_credentials(api_client):
    response = api_client.get("/")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")
    assert api_client.get("/", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401
    response = api_client.get("/", auth=("owner", PASSWORD))
    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert TOKEN not in response.text
    assert PASSWORD not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1},
    {"kri_min": 101}, {"fml200_min": "nan"}, {"min_starts": 0}, {"max_dlr": -1},
    {"date_from": "2026-11-02", "date_to": "2026-11-01"}])
def test_invalid_parameters_fail_with_422(api_client, params):
    assert api_client.get("/api/class-droppers", params=params,
                          headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 422


def test_html_forms_accept_empty_optional_dates_and_track(api_client):
    response = api_client.get("/", params={"date_from": "", "date_to": "", "track": ""},
                              auth=("owner", PASSWORD))
    assert response.status_code == 200


def test_health_and_missing_runner(api_client):
    assert api_client.get("/health/live").json() == {"status": "ok"}
    assert api_client.get("/health/ready").status_code == 200
    assert api_client.get("/api/runners/missing/sectionals",
                          headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


def test_browser_sectionals_and_trainer_reports_use_basic_auth(api_client):
    assert api_client.get("/runners/runner/sectionals").status_code == 401
    assert api_client.get("/runners/runner/sectionals", auth=("owner", PASSWORD)).status_code == 200
    for report in ["first-up", "backup", "elite-closers"]:
        response = api_client.get("/", params={"report": report}, auth=("owner", PASSWORD))
        assert response.status_code == 200
    assert api_client.get("/", params={"report": "unknown"},
                          auth=("owner", PASSWORD)).status_code == 422


def test_literal_track_filter_does_not_inject_sql(api_client):
    response = api_client.get("/api/class-droppers", params={"track": "Ascot' OR 1=1 --"},
                              headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_missing_or_short_auth_configuration_fails_closed():
    from punting.api import create_app
    from punting.config import Settings

    for token, password in [("", ""), ("short", "short")]:
        settings = Settings(api_token=token, admin_password=password)
        with TestClient(create_app(settings=settings)) as client:
            assert client.get("/health/live").status_code == 200
            assert client.get("/api/class-droppers",
                              headers={"Authorization": f"Bearer {token}"}).status_code == 503
            assert client.get("/", auth=("admin", password)).status_code == 503
