"""Serialize schema upgrades before accepting API traffic."""

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from punting.config import Settings


def main() -> None:
    engine = create_engine(Settings().database_url)
    try:
        with engine.connect() as connection:
            # Keep a dedicated session lock while Alembic uses its own session.
            # A failed or killed process releases its lock automatically.
            if engine.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_lock(731730285404)"))
            try:
                command.upgrade(Config("/app/alembic.ini"), "head")
            finally:
                if engine.dialect.name == "postgresql":
                    connection.execute(text("SELECT pg_advisory_unlock(731730285404)"))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
