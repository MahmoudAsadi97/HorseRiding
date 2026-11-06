"""Parameterized warehouse views. Acquisition never depends on these filters."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, case, extract, func, or_, select
from sqlalchemy.orm import Session

from punting.models import Meeting, Race, Runner, Sectional200m


class QueryFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    state: str = Field(default="WA", min_length=2, max_length=3, pattern=r"^[A-Za-z]+$")
    track: str | None = Field(default=None, max_length=120)
    date_from: date | None = None
    date_to: date | None = None
    saturday_only: bool = False
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    cls_diff_max: float = Field(default=0, ge=-1000, le=1000)
    kri_min: float = Field(default=70, ge=0, le=100)
    fml200_min: float = Field(default=2.5, ge=-1000, le=1000)
    run_styles: list[str] = Field(default_factory=lambda: ["Mid-Field", "Backmarker"],
                                  min_length=1, max_length=10)
    min_starts: int = Field(default=1, ge=1, le=1_000_000)
    max_dlr: int = Field(default=7, ge=0, le=365)

    @field_validator("date_from", "date_to", "track", mode="before")
    @classmethod
    def optional_form_value(cls, value):
        return None if value == "" else value

    @field_validator("state")
    @classmethod
    def normalize_state(cls, value):
        return value.upper()

    @field_validator("run_styles")
    @classmethod
    def bounded_styles(cls, values):
        if any(not value.strip() or len(value) > 40 for value in values):
            raise ValueError("Each run style must contain 1–40 characters")
        return values

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


def meeting_conditions(filters: QueryFilters):
    conditions = []
    if filters.state != "ALL":
        conditions.append(Meeting.state == filters.state)
    if filters.track:
        conditions.append(func.lower(Meeting.track) == filters.track.lower())
    if filters.date_from:
        conditions.append(Meeting.date >= filters.date_from)
    if filters.date_to:
        conditions.append(Meeting.date <= filters.date_to)
    if filters.saturday_only:
        conditions.append(extract("dow", Meeting.date) == 6)
    return conditions


RUNNER_FIELDS = (
    "id", "horse_name", "barrier", "weight", "trainer", "jockey", "prse", "run_style",
    "kri", "cls_diff", "fml600", "fml200", "rb", "dlr", "finish_position", "starting_price",
    "scratched", "dead_heat_factor", "result_status", "settled_win_return",
)


def runner_statement(filters: QueryFilters):
    return select(
        *(getattr(Runner, field) for field in RUNNER_FIELDS),
        Race.id.label("race_id"), Race.number.label("race_number"), Race.distance,
        Meeting.id.label("meeting_id"), Meeting.date.label("meeting_date"),
        Meeting.track, Meeting.state,
    ).join(Race, Runner.race_id == Race.id).join(
        Meeting, Race.meeting_id == Meeting.id
    ).where(*meeting_conditions(filters))


def page(session: Session, statement, filters: QueryFilters):
    total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    items = [dict(row) for row in session.execute(
        statement.limit(filters.limit).offset(filters.offset)
    ).mappings()]
    return {"items": items, "total": total, "limit": filters.limit, "offset": filters.offset}


def class_droppers(session: Session, filters: QueryFilters):
    statement = runner_statement(filters).where(
        Runner.cls_diff < filters.cls_diff_max, Runner.kri >= filters.kri_min,
    ).order_by(Meeting.date, Meeting.track, Meeting.id, Race.number, Race.id, Runner.id)
    return page(session, statement, filters)


def elite_closers(session: Session, filters: QueryFilters):
    statement = runner_statement(filters).where(
        Runner.fml200 >= filters.fml200_min, Runner.run_style.in_(filters.run_styles),
    ).order_by(Meeting.date, Meeting.track, Meeting.id, Race.number, Race.id, Runner.id)
    return page(session, statement, filters)


def trainer_report(session: Session, filters: QueryFilters,
                   pattern: Literal["first_up", "backup"]):
    """Flat one-unit stakes; missing prices do not enter ROI's denominator."""
    if pattern not in {"first_up", "backup"}:
        raise ValueError("Unknown trainer pattern")
    settled = or_(Runner.result_status.in_(["finished", "dnf", "disqualified"]),
                  and_(Runner.result_status.is_(None), Runner.finish_position > 0))
    winner = and_(Runner.finish_position == 1,
                  or_(Runner.result_status.is_(None), Runner.result_status == "finished"))
    priced = Runner.starting_price >= 1
    starts = func.count(Runner.id)
    wins = func.sum(case((winner, 1), else_=0))
    priced_starts = func.sum(case((priced, 1), else_=0))
    priced_wins = func.sum(case((and_(priced, winner), 1), else_=0))
    returns = func.sum(case(
        (and_(priced, winner), func.coalesce(
            Runner.settled_win_return,
            Runner.starting_price * func.coalesce(Runner.dead_heat_factor, 1),
        )),
        else_=0,
    ))
    pattern_condition = Runner.rb == 0 if pattern == "first_up" else and_(
        Runner.dlr >= 0, Runner.dlr <= filters.max_dlr,
    )
    statement = select(
        Runner.trainer, starts.label("starts"), wins.label("wins"),
        priced_starts.label("priced_starts"), priced_wins.label("priced_wins"),
        returns.label("return_units"),
    ).join(Race, Runner.race_id == Race.id).join(Meeting, Race.meeting_id == Meeting.id).where(
        *meeting_conditions(filters), settled, Runner.scratched.is_(False),
        Runner.trainer.is_not(None), func.trim(Runner.trainer) != "", pattern_condition,
    ).group_by(Runner.trainer).having(starts >= filters.min_starts).order_by(
        starts.desc(), Runner.trainer,
    )
    report = page(session, statement, filters)
    for item in report["items"]:
        item["unpriced_starts"] = item["starts"] - item["priced_starts"]
        item["strike_rate_pct"] = round(100 * item["wins"] / item["starts"], 4)
        item["return_units"] = float(item["return_units"])
        item["profit_units"] = round(item["return_units"] - item["priced_starts"], 6)
        item["roi_pct"] = (round(100 * item["profit_units"] / item["priced_starts"], 4)
                           if item["priced_starts"] else None)
    report["methodology"] = {
        "starts": "Settled finished/DNF/disqualified entries; positive placing fallback when "
                  "status is absent. "
                  "Scratches and unknown trainer names are excluded.",
        "strike_rate": "Wins / settled starts × 100; each dead-heat winner counts as one win.",
        "roi": "(Gross returns for priced winners − priced starts) / priced starts × 100. "
               "Use actual settled payout per unit when recorded; otherwise decimal SP × "
               "dead-heat factor. Actual payouts already include any dead-heat adjustment. "
               "One unit per priced start; SP below 1 or absent excluded. "
               "No priced starts produces null ROI.",
        "pattern": "RB = 0" if pattern == "first_up" else f"0 ≤ DLR ≤ {filters.max_dlr}",
    }
    return report


def runner_sectionals(session: Session, runner_id: str, filters: QueryFilters):
    runner = session.execute(runner_statement(filters).where(Runner.id == runner_id)).mappings().first()
    if runner is None:
        return None
    columns = ("id", "runner_id", "sequence", "distance", "sectional_time", "increment",
               "raw_last600", "raw_last200", "fml600", "fml200")
    statement = select(*(getattr(Sectional200m, field) for field in columns)).where(
        Sectional200m.runner_id == runner_id,
    ).order_by(Sectional200m.sequence, Sectional200m.id)
    report = page(session, statement, filters)
    report["runner"] = dict(runner)
    return report


def meetings(session: Session, filters: QueryFilters):
    columns = ("id", "date", "track", "state", "rail_position", "track_condition",
               "moisture_variant")
    statement = select(*(getattr(Meeting, field) for field in columns)).where(
        *meeting_conditions(filters),
    ).order_by(Meeting.date, Meeting.track, Meeting.id)
    return page(session, statement, filters)
