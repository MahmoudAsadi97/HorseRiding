"""Portable relational projections and an immutable source-response archive."""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class RawFeed(Base):
    __tablename__ = "raw_feeds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    response_status: Mapped[int] = mapped_column(Integer, default=200)
    raw_body: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[Any | None] = mapped_column(JSON_TYPE, nullable=True)
    source_context: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    normalization_status: Mapped[str] = mapped_column(String(20), default="pending")
    normalization_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_raw_feeds_received_at", "received_at"),)


class Meeting(Base):
    __tablename__ = "meetings"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    date: Mapped[date] = mapped_column(Date)
    track: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(20))
    rail_position: Mapped[str | None] = mapped_column(String(200))
    track_condition: Mapped[str | None] = mapped_column(String(200))
    moisture_variant: Mapped[float | None] = mapped_column(Float)
    raw_payload: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    source_feed_id: Mapped[int | None] = mapped_column(ForeignKey("raw_feeds.id"))
    __table_args__ = (
        Index("ix_meetings_state_date", "state", "date"),
        Index("ix_meetings_track_date", "track", "date"),
    )


class Race(Base):
    __tablename__ = "races"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    distance: Mapped[int] = mapped_column(Integer)
    class_grade: Mapped[str | None] = mapped_column(String(200))
    class_par: Mapped[float | None] = mapped_column(Float)
    raw_payload: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    source_feed_id: Mapped[int | None] = mapped_column(ForeignKey("raw_feeds.id"))
    __table_args__ = (
        CheckConstraint("number > 0", name="ck_race_number"),
        CheckConstraint("distance > 0", name="ck_race_distance"),
    )


class Runner(Base):
    __tablename__ = "runners"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    race_id: Mapped[str] = mapped_column(ForeignKey("races.id"), index=True)
    horse_name: Mapped[str] = mapped_column(String(300))
    barrier: Mapped[int | None] = mapped_column(Integer)
    weight: Mapped[float | None] = mapped_column(Float)
    trainer: Mapped[str | None] = mapped_column(String(300), index=True)
    jockey: Mapped[str | None] = mapped_column(String(300))
    prse: Mapped[float | None] = mapped_column(Float)
    run_style: Mapped[str | None] = mapped_column(String(100))
    kri: Mapped[float | None] = mapped_column(Float)
    cls_diff: Mapped[float | None] = mapped_column(Float)
    fml600: Mapped[float | None] = mapped_column(Float)
    fml200: Mapped[float | None] = mapped_column(Float)
    rb: Mapped[int | None] = mapped_column(Integer)
    dlr: Mapped[int | None] = mapped_column(Integer)
    finish_position: Mapped[int | None] = mapped_column(Integer)
    starting_price: Mapped[float | None] = mapped_column(Float)
    settled_win_return: Mapped[float | None] = mapped_column(Float)
    scratched: Mapped[bool] = mapped_column(Boolean, default=False)
    dead_heat_factor: Mapped[float] = mapped_column(Float, default=1.0)
    result_status: Mapped[str | None] = mapped_column(String(30))
    raw_payload: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    source_feed_id: Mapped[int | None] = mapped_column(ForeignKey("raw_feeds.id"))
    __table_args__ = (
        Index("ix_runners_class_dropper", "cls_diff", "kri"),
        Index("ix_runners_closers", "run_style", "fml200"),
        Index("ix_runners_trainer_rb_dlr", "trainer", "rb", "dlr"),
        CheckConstraint(
            "dead_heat_factor > 0 AND dead_heat_factor <= 1", name="ck_runner_dead_heat"
        ),
        CheckConstraint(
            "settled_win_return IS NULL OR settled_win_return >= 0", name="ck_runner_settled_return"
        ),
        CheckConstraint("rb IS NULL OR rb >= 0", name="ck_runner_rb"),
        CheckConstraint("dlr IS NULL OR dlr >= 0", name="ck_runner_dlr"),
    )


class Sectional200m(Base):
    __tablename__ = "sectionals_200m"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    runner_id: Mapped[str] = mapped_column(ForeignKey("runners.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    distance: Mapped[int] = mapped_column(Integer)
    sectional_time: Mapped[float | None] = mapped_column(Float)
    increment: Mapped[float | None] = mapped_column(Float)
    raw_last600: Mapped[float | None] = mapped_column(Float)
    raw_last200: Mapped[float | None] = mapped_column(Float)
    fml600: Mapped[float | None] = mapped_column(Float)
    fml200: Mapped[float | None] = mapped_column(Float)
    raw_payload: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    source_feed_id: Mapped[int | None] = mapped_column(ForeignKey("raw_feeds.id"))
    __table_args__ = (
        Index("ix_sectionals_runner_sequence", "runner_id", "sequence", unique=True),
        CheckConstraint("sequence > 0", name="ck_sectional_sequence"),
        CheckConstraint("distance > 0 AND distance % 200 = 0", name="ck_sectional_distance"),
    )
