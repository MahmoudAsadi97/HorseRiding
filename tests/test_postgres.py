"""Real PostgreSQL contract tests; set TEST_DATABASE_URL to an admin test connection."""
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL required')


@pytest.fixture(scope='module')
def pg_engine():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.engine import make_url
    url = make_url(os.environ['TEST_DATABASE_URL'])
    name = 'punting_test_' + uuid.uuid4().hex
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE {name}'))
    engine = create_engine(url.set(database=name))
    old = os.environ.get('DATABASE_URL')
    os.environ['DATABASE_URL'] = url.set(database=name).render_as_string(hide_password=False)
    try:
        config = Config(str(Path(__file__).parents[1] / 'alembic.ini'))
        command.upgrade(config, 'head')
        command.upgrade(config, 'head')
        yield engine
    finally:
        if old is None:
            os.environ.pop('DATABASE_URL', None)
        else:
            os.environ['DATABASE_URL'] = old
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE {name} WITH (FORCE)'))
        admin.dispose()


def test_postgresql_migration_indexes_and_jsonb(pg_engine):
    i = inspect(pg_engine)
    assert {'meetings', 'races', 'runners', 'sectionals_200m', 'raw_feeds'} <= set(i.get_table_names())
    assert any(x['column_names'] == ['runner_id', 'sequence'] and x['unique'] for x in i.get_indexes('sectionals_200m'))
    columns = {x['name']: str(x['type']) for x in i.get_columns('raw_feeds')}
    assert columns['payload'] == 'JSONB'
    assert columns['raw_body'] == 'BYTEA'


def test_postgresql_raw_ingestion_and_replay(pg_engine):
    import json

    from punting.ingest import ingest_payload
    from punting.models import RawFeed, Runner
    payload = json.loads((Path(__file__).parent / 'fixtures/demo.json').read_text())
    body = json.dumps(payload, indent=2).encode()
    with Session(pg_engine) as session:
        ingest_payload(session, 'postgres-synthetic-contract', payload, raw_body=body)
        before = len(session.scalars(select(Runner)).all())
        assert before > 0
        ingest_payload(session, 'postgres-synthetic-contract', payload, raw_body=body)
        assert len(session.scalars(select(Runner)).all()) == before
        feeds = session.scalars(select(RawFeed)).all()
        assert len(feeds) == 2
        assert all(x.raw_body == body and x.payload == payload for x in feeds)


def test_postgresql_archive_immutability(pg_engine):
    with pg_engine.connect() as conn:
        with pytest.raises(DatabaseError):
            conn.execute(text("UPDATE raw_feeds SET raw_body = 'changed'"))
        conn.rollback()
        with pytest.raises(DatabaseError):
            conn.execute(text('DELETE FROM raw_feeds'))
        conn.rollback()


def test_postgresql_foreign_keys(pg_engine):
    from punting.models import Race
    with Session(pg_engine) as session, pytest.raises(IntegrityError):
        session.add(Race(id='orphan', meeting_id='absent', number=1, distance=1200, raw_payload={}))
        session.commit()


@pytest.mark.parametrize('raw_body', [b'{"value":NaN}', b'{"value":Infinity}', b'{"value":"\\u0000"}', b'{"value":1e100000}'])
def test_postgresql_unrepresentable_json_still_preserves_raw_bytes(pg_engine, raw_body):
    from punting.ingest import IngestionError, ingest_response
    from punting.models import RawFeed
    with Session(pg_engine) as session, pytest.raises(IngestionError):
        ingest_response(session, 'unrepresentable-json', raw_body)
    with Session(pg_engine) as session:
        feed = session.scalars(select(RawFeed).order_by(RawFeed.id.desc())).first()
        assert feed.raw_body == raw_body
        assert feed.payload is None
        assert feed.normalization_status == 'failed'


def test_postgresql_canonical_nan_still_preserves_raw_bytes(pg_engine):
    from punting.ingest import IngestionError, ingest_payload
    from punting.models import RawFeed
    with Session(pg_engine) as session, pytest.raises(IngestionError):
        ingest_payload(session, 'canonical-nan', {'meetings': [], 'value': float('nan')})
    with Session(pg_engine) as session:
        feed = session.scalars(select(RawFeed).order_by(RawFeed.id.desc())).first()
        assert b'NaN' in feed.raw_body
        assert feed.payload is None
        assert feed.normalization_status == 'failed'
