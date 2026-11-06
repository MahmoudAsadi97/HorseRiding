"""Synthetic records exercise thresholds and financial denominators."""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def query_session():
    from punting.models import Base, Meeting, Race, Runner

    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for key, state, track, day in [
            ("wa", "WA", "Ascot", date(2026, 10, 31)),
            ("nsw", "NSW", "Randwick", date(2026, 10, 31)),
            ("sun", "WA", "Bunbury", date(2026, 11, 1)),
        ]:
            session.add(Meeting(id=key, date=day, track=track, state=state, raw_payload={}))
            session.add(Race(id=key, meeting_id=key, number=1, distance=1200, raw_payload={}))
        session.flush()
        for key, attrs in [
            ("qualifies", {}),
            ("class_boundary", {"cls_diff": 0}),
            ("low_kri", {"kri": 69.99}),
            ("null_kri", {"kri": None}),
            ("null_class", {"cls_diff": None}),
            ("slow", {"fml200": 2.49}),
            ("leader", {"run_style": "Leader"}),
            ("null_sectional", {"fml200": None}),
            ("backmarker", {"run_style": "Backmarker"}),
            ("nsw_runner", {"race_id": "nsw"}),
            ("sunday_runner", {"race_id": "sun"}),
        ]:
            values = {"id": key, "race_id": "wa", "horse_name": key, "trainer": "Trainer A",
                      "cls_diff": -1, "kri": 70, "fml200": 2.5, "run_style": "Mid-Field",
                      "scratched": False, "raw_payload": {}}
            values.update(attrs)
            session.add(Runner(**values))
        session.commit()
        yield session
    engine.dispose()


def test_class_scanner_strict_boundary_nulls_and_default_wa(query_session):
    from punting.queries import QueryFilters, class_droppers

    result = class_droppers(query_session, QueryFilters())
    ids = {item["id"] for item in result["items"]}
    assert ids == {"qualifies", "slow", "leader", "null_sectional", "backmarker",
                   "sunday_runner"}
    assert result["total"] == 6


def test_elite_scanner_exact_threshold_styles_and_nulls(query_session):
    from punting.queries import QueryFilters, elite_closers

    result = elite_closers(query_session, QueryFilters(track="Ascot"))
    assert {item["id"] for item in result["items"]} == {
        "qualifies", "class_boundary", "low_kri", "null_kri", "null_class", "backmarker"
    }


def test_dynamic_filters_dates_saturday_and_stable_pagination(query_session):
    from punting.queries import QueryFilters, class_droppers, elite_closers

    relaxed = class_droppers(query_session, QueryFilters(
        state="NSW", cls_diff_max=0, kri_min=60, date_from=date(2026, 10, 31),
        date_to=date(2026, 10, 31), saturday_only=True))
    assert [item["id"] for item in relaxed["items"]] == ["nsw_runner"]
    assert elite_closers(query_session, QueryFilters(fml200_min=3))["total"] == 0
    strict = class_droppers(query_session, QueryFilters(cls_diff_max=-1))
    assert strict["total"] == 0
    full = class_droppers(query_session, QueryFilters(saturday_only=True))
    page = class_droppers(query_session, QueryFilters(saturday_only=True, limit=2, offset=2))
    assert page["total"] == 5
    assert page["items"] == full["items"][2:4]
    assert [item["id"] for item in full["items"]] == [
        "backmarker", "leader", "null_sectional", "qualifies", "slow"
    ]


def test_trainer_settled_starts_priced_roi_dead_heat_and_dnf(query_session):
    from punting.models import Runner
    from punting.queries import QueryFilters, trainer_report

    records = [
        ("winner", {"finish_position": 1, "starting_price": 4}),
        ("deadheat", {"finish_position": 1, "starting_price": 6, "dead_heat_factor": .5}),
        ("loss", {"finish_position": 2, "starting_price": 2}),
        ("unpriced", {"finish_position": 1, "starting_price": None}),
        ("dnf", {"result_status": "dnf", "starting_price": 8}),
        ("pending", {"starting_price": 3}),
        ("provisional", {"result_status": "provisional", "finish_position": 1}),
        ("scratch", {"finish_position": 1, "starting_price": 10, "scratched": True}),
        ("not_firstup", {"finish_position": 1, "starting_price": 20, "rb": 1}),
        ("outside_wa", {"race_id": "nsw", "finish_position": 1, "starting_price": 20}),
    ]
    for key, attrs in records:
        values = {"id": key, "race_id": "wa", "horse_name": key, "trainer": "Results Trainer",
                  "rb": 0, "dlr": 7, "scratched": False, "raw_payload": {}}
        values.update(attrs)
        query_session.add(Runner(**values))
    query_session.commit()
    report = trainer_report(query_session, QueryFilters(), pattern="first_up")
    assert report["total"] == 1
    item = report["items"][0]
    assert item["starts"] == 5
    assert item["wins"] == 3
    assert item["strike_rate_pct"] == 60
    assert item["priced_starts"] == 4
    assert item["unpriced_starts"] == 1
    assert item["priced_wins"] == 2
    assert item["return_units"] == 7
    assert item["profit_units"] == 3
    assert item["roi_pct"] == 75
    assert trainer_report(query_session, QueryFilters(min_starts=6),
                          pattern="first_up")["items"] == []


def test_backup_boundaries_and_unpriced_roi_is_null(query_session):
    from punting.models import Runner
    from punting.queries import QueryFilters, trainer_report

    for index, days in enumerate([0, 7, 8, None]):
        query_session.add(Runner(id=f"backup{index}", race_id="wa", horse_name="Backup",
                                 trainer="Backup Trainer", dlr=days, finish_position=2,
                                 scratched=False, raw_payload={}))
    query_session.commit()
    report = trainer_report(query_session, QueryFilters(), pattern="backup")
    assert report["items"][0]["starts"] == 2
    assert report["items"][0]["priced_starts"] == 0
    assert report["items"][0]["roi_pct"] is None
    assert trainer_report(query_session, QueryFilters(max_dlr=0),
                          pattern="backup")["items"][0]["starts"] == 1


def test_negative_backup_days_cannot_enter_the_relational_warehouse(query_session):
    from sqlalchemy.exc import IntegrityError

    from punting.models import Runner

    query_session.add(Runner(id="negative", race_id="wa", horse_name="Invalid DLR",
                             trainer="Backup Trainer", dlr=-1, finish_position=2,
                             scratched=False, raw_payload={}))
    with pytest.raises(IntegrityError, match="ck_runner_dlr"):
        query_session.flush()
    query_session.rollback()


def test_actual_settled_payout_overrides_odds_without_double_deadheat_discount(query_session):
    from punting.models import Runner
    from punting.queries import QueryFilters, trainer_report

    query_session.add(Runner(id="dividend-winner", race_id="wa", horse_name="Dividend winner",
                             trainer="Dividend Trainer", rb=0, finish_position=1,
                             starting_price=4, settled_win_return=3.5, dead_heat_factor=.5,
                             scratched=False, raw_payload={}))
    query_session.add(Runner(id="dividend-loser", race_id="wa", horse_name="Dividend loser",
                             trainer="Dividend Trainer", rb=0, finish_position=2,
                             starting_price=2, scratched=False, raw_payload={}))
    query_session.commit()
    item = trainer_report(query_session, QueryFilters(), pattern="first_up")["items"][0]
    assert item["starts"] == 2
    assert item["priced_starts"] == 2
    assert item["return_units"] == 3.5
    assert item["roi_pct"] == 75


def test_trainer_date_filters_min_starts_and_deterministic_pages(query_session):
    from punting.models import Runner
    from punting.queries import QueryFilters, trainer_report

    for key, race_id, trainer in [
        ("a-one", "wa", "A Trainer"), ("a-two", "sun", "A Trainer"),
        ("b-one", "wa", "B Trainer"), ("c-one", "wa", "C Trainer"),
    ]:
        query_session.add(Runner(id=key, race_id=race_id, horse_name=key, trainer=trainer,
                                 rb=0, dlr=7, finish_position=2, starting_price=2,
                                 scratched=False, raw_payload={}))
    query_session.commit()
    first = trainer_report(query_session, QueryFilters(limit=1), pattern="first_up")
    second = trainer_report(query_session, QueryFilters(limit=1, offset=1), pattern="first_up")
    assert first["total"] == 3
    assert first["items"][0]["trainer"] == "A Trainer"
    assert first["items"][0]["starts"] == 2
    assert second["items"][0]["trainer"] == "B Trainer"
    sunday = trainer_report(query_session, QueryFilters(date_from=date(2026, 11, 1)),
                            pattern="first_up")
    assert sunday["total"] == 1
    assert sunday["items"][0]["starts"] == 1
    minimum = trainer_report(query_session, QueryFilters(min_starts=2), pattern="first_up")
    assert minimum["total"] == 1
    saturday = trainer_report(query_session, QueryFilters(saturday_only=True), pattern="backup")
    assert [item["trainer"] for item in saturday["items"]] == [
        "A Trainer", "B Trainer", "C Trainer"
    ]


def test_disqualified_settled_start_is_a_loss_in_roi_denominator(query_session):
    from punting.models import Runner
    from punting.queries import QueryFilters, trainer_report

    for key, status, placing in [("result-win", "finished", 1),
                                 ("result-dq", "disqualified", 1)]:
        query_session.add(Runner(id=key, race_id="wa", horse_name=key,
                                 trainer="Disqualification Trainer", rb=0, dlr=7,
                                 result_status=status, finish_position=placing, starting_price=2,
                                 scratched=False, raw_payload={}))
    query_session.commit()
    item = trainer_report(query_session, QueryFilters(), pattern="first_up")["items"][0]
    assert item["starts"] == 2
    assert item["wins"] == 1
    assert item["priced_starts"] == 2
    assert item["return_units"] == 2
    assert item["roi_pct"] == 0


def test_sectionals_are_sequential_and_scoped_to_state(query_session):
    from punting.models import Sectional200m
    from punting.queries import QueryFilters, runner_sectionals

    for sequence in [3, 1, 2]:
        query_session.add(Sectional200m(runner_id="qualifies", sequence=sequence,
                                       distance=200 * sequence, sectional_time=12,
                                       raw_payload={}))
    query_session.commit()
    result = runner_sectionals(query_session, "qualifies", QueryFilters())
    assert [item["sequence"] for item in result["items"]] == [1, 2, 3]
    assert result["runner"]["horse_name"] == "qualifies"
    assert runner_sectionals(query_session, "qualifies", QueryFilters(state="NSW")) is None
