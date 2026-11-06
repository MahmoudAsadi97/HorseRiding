"""Database setup. Runtime production schema changes are managed by Alembic."""

from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from punting.models import Base

ARCHIVE_COLUMNS = (
    "endpoint",
    "received_at",
    "response_status",
    "raw_body",
    "sha256",
    "payload",
    "source_context",
)


def make_engine(url: str):
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection, _):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def protect_archive(connection):
    """Reject changes to source evidence, allowing only normalization bookkeeping."""
    if connection.dialect.name == "sqlite":
        columns = ", ".join(ARCHIVE_COLUMNS)
        connection.exec_driver_sql(f"""CREATE TRIGGER IF NOT EXISTS raw_feeds_immutable_update
            BEFORE UPDATE OF {columns} ON raw_feeds BEGIN
            SELECT RAISE(ABORT, 'raw archive is immutable'); END""")
        connection.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS raw_feeds_immutable_delete
            BEFORE DELETE ON raw_feeds BEGIN
            SELECT RAISE(ABORT, 'raw archive is immutable'); END""")
    elif connection.dialect.name == "postgresql":
        comparison = " OR ".join(
            f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in ARCHIVE_COLUMNS
        )
        connection.exec_driver_sql(f"""CREATE OR REPLACE FUNCTION prevent_raw_feed_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'raw archive is immutable'; END IF;
            IF {comparison} THEN RAISE EXCEPTION 'raw archive is immutable'; END IF;
            RETURN NEW; END $$""")
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS raw_feeds_immutable ON raw_feeds")
        connection.exec_driver_sql("""CREATE TRIGGER raw_feeds_immutable BEFORE UPDATE OR DELETE
            ON raw_feeds FOR EACH ROW EXECUTE FUNCTION prevent_raw_feed_mutation()""")


def create_schema(engine):
    """Create a local/test schema. Production uses `alembic upgrade head`."""
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        protect_archive(connection)
