import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from punting.db import create_schema, make_engine
from punting.models import Race


def test_foreign_keys_reject_orphan_races():
    engine = make_engine("sqlite://")
    create_schema(engine)
    with Session(engine) as session, pytest.raises(IntegrityError):
        session.add(Race(id="r", meeting_id="missing", number=1, distance=1200, raw_payload={}))
        session.commit()


def test_sectionals_have_unique_runner_sequence_and_query_indexes():
    engine = make_engine("sqlite://")
    create_schema(engine)
    inspector = inspect(engine)
    assert any(
        index["column_names"] == ["runner_id", "sequence"] and index["unique"]
        for index in inspector.get_indexes("sectionals_200m")
    )
    assert any(
        index["column_names"] == ["state", "date"] for index in inspector.get_indexes("meetings")
    )
    assert any(index["column_names"] == ["race_id"] for index in inspector.get_indexes("runners"))


def test_alembic_upgrade_is_repeatable_on_empty_database(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/migration.db")
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    engine = make_engine(f"sqlite:///{tmp_path}/migration.db")
    assert {"meetings", "races", "runners", "sectionals_200m", "raw_feeds"} <= set(
        inspect(engine).get_table_names()
    )


def test_upgrade_existing_initial_schema_adds_settled_returns(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    url = f"sqlite:///{tmp_path}/existing.db"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "0001")
    engine = make_engine(url)
    assert "settled_win_return" not in {
        column["name"] for column in inspect(engine).get_columns("runners")
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO meetings (id, date, track, state, raw_payload) VALUES ('m', '2026-10-31', 'Ascot', 'WA', '{}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO races (id, meeting_id, number, distance, raw_payload) VALUES ('r', 'm', 1, 1200, '{}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO runners (id, race_id, horse_name, scratched, dead_heat_factor, raw_payload) VALUES ('h', 'r', 'Existing Runner', false, 1, '{}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sectionals_200m (runner_id, sequence, distance, raw_payload) VALUES ('h', 1, 200, '{}')"
            )
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT horse_name, settled_win_return FROM runners WHERE id='h'")
        ).one() == ("Existing Runner", None)
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM sectionals_200m WHERE runner_id='h'")
            ).scalar()
            == 1
        )
    assert "settled_win_return" in {
        column["name"] for column in inspect(engine).get_columns("runners")
    }
    assert any(
        constraint["name"] == "ck_runner_settled_return"
        for constraint in inspect(engine).get_check_constraints("runners")
    )
