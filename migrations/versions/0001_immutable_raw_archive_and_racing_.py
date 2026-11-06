"""Immutable raw archive and racing projections

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "raw_feeds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("raw_body", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "source_context",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("normalization_status", sa.String(length=20), nullable=False),
        sa.Column("normalization_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_raw_feeds_received_at", "raw_feeds", ["received_at"], unique=False)
    op.create_index(op.f("ix_raw_feeds_sha256"), "raw_feeds", ["sha256"], unique=False)
    op.create_table(
        "meetings",
        sa.Column("id", sa.String(length=200), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("track", sa.String(length=200), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("rail_position", sa.String(length=200), nullable=True),
        sa.Column("track_condition", sa.String(length=200), nullable=True),
        sa.Column("moisture_variant", sa.Float(), nullable=True),
        sa.Column(
            "raw_payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("source_feed_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_feed_id"],
            ["raw_feeds.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meetings_state_date", "meetings", ["state", "date"], unique=False)
    op.create_index("ix_meetings_track_date", "meetings", ["track", "date"], unique=False)
    op.create_table(
        "races",
        sa.Column("id", sa.String(length=200), nullable=False),
        sa.Column("meeting_id", sa.String(length=200), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("distance", sa.Integer(), nullable=False),
        sa.Column("class_grade", sa.String(length=200), nullable=True),
        sa.Column("class_par", sa.Float(), nullable=True),
        sa.Column(
            "raw_payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("source_feed_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("distance > 0", name="ck_race_distance"),
        sa.CheckConstraint("number > 0", name="ck_race_number"),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["meetings.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_feed_id"],
            ["raw_feeds.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_races_meeting_id"), "races", ["meeting_id"], unique=False)
    op.create_table(
        "runners",
        sa.Column("id", sa.String(length=200), nullable=False),
        sa.Column("race_id", sa.String(length=200), nullable=False),
        sa.Column("horse_name", sa.String(length=300), nullable=False),
        sa.Column("barrier", sa.Integer(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("trainer", sa.String(length=300), nullable=True),
        sa.Column("jockey", sa.String(length=300), nullable=True),
        sa.Column("prse", sa.Float(), nullable=True),
        sa.Column("run_style", sa.String(length=100), nullable=True),
        sa.Column("kri", sa.Float(), nullable=True),
        sa.Column("cls_diff", sa.Float(), nullable=True),
        sa.Column("fml600", sa.Float(), nullable=True),
        sa.Column("fml200", sa.Float(), nullable=True),
        sa.Column("rb", sa.Integer(), nullable=True),
        sa.Column("dlr", sa.Integer(), nullable=True),
        sa.Column("finish_position", sa.Integer(), nullable=True),
        sa.Column("starting_price", sa.Float(), nullable=True),
        sa.Column("scratched", sa.Boolean(), nullable=False),
        sa.Column("dead_heat_factor", sa.Float(), nullable=False),
        sa.Column("result_status", sa.String(length=30), nullable=True),
        sa.Column(
            "raw_payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("source_feed_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "dead_heat_factor > 0 AND dead_heat_factor <= 1", name="ck_runner_dead_heat"
        ),
        sa.CheckConstraint("dlr IS NULL OR dlr >= 0", name="ck_runner_dlr"),
        sa.CheckConstraint("rb IS NULL OR rb >= 0", name="ck_runner_rb"),
        sa.ForeignKeyConstraint(
            ["race_id"],
            ["races.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_feed_id"],
            ["raw_feeds.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runners_class_dropper", "runners", ["cls_diff", "kri"], unique=False)
    op.create_index("ix_runners_closers", "runners", ["run_style", "fml200"], unique=False)
    op.create_index(op.f("ix_runners_race_id"), "runners", ["race_id"], unique=False)
    op.create_index(op.f("ix_runners_trainer"), "runners", ["trainer"], unique=False)
    op.create_index("ix_runners_trainer_rb_dlr", "runners", ["trainer", "rb", "dlr"], unique=False)
    op.create_table(
        "sectionals_200m",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("runner_id", sa.String(length=200), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("distance", sa.Integer(), nullable=False),
        sa.Column("sectional_time", sa.Float(), nullable=True),
        sa.Column("increment", sa.Float(), nullable=True),
        sa.Column("raw_last600", sa.Float(), nullable=True),
        sa.Column("raw_last200", sa.Float(), nullable=True),
        sa.Column("fml600", sa.Float(), nullable=True),
        sa.Column("fml200", sa.Float(), nullable=True),
        sa.Column(
            "raw_payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("source_feed_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("distance > 0 AND distance % 200 = 0", name="ck_sectional_distance"),
        sa.CheckConstraint("sequence > 0", name="ck_sectional_sequence"),
        sa.ForeignKeyConstraint(
            ["runner_id"],
            ["runners.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_feed_id"],
            ["raw_feeds.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sectionals_runner_sequence", "sectionals_200m", ["runner_id", "sequence"], unique=True
    )
    # Freeze this protection with the initial migration rather than importing runtime code.
    connection = op.get_bind()
    columns = (
        "endpoint",
        "received_at",
        "response_status",
        "raw_body",
        "sha256",
        "payload",
        "source_context",
    )
    if connection.dialect.name == "sqlite":
        op.execute(f"""CREATE TRIGGER raw_feeds_immutable_update
            BEFORE UPDATE OF {", ".join(columns)} ON raw_feeds BEGIN
            SELECT RAISE(ABORT, 'raw archive is immutable'); END""")
        op.execute("""CREATE TRIGGER raw_feeds_immutable_delete
            BEFORE DELETE ON raw_feeds BEGIN
            SELECT RAISE(ABORT, 'raw archive is immutable'); END""")
    elif connection.dialect.name == "postgresql":
        comparison = " OR ".join(
            f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in columns
        )
        op.execute(f"""CREATE FUNCTION prevent_raw_feed_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'raw archive is immutable'; END IF;
            IF {comparison} THEN RAISE EXCEPTION 'raw archive is immutable'; END IF;
            RETURN NEW; END $$""")
        op.execute("""CREATE TRIGGER raw_feeds_immutable BEFORE UPDATE OR DELETE
            ON raw_feeds FOR EACH ROW EXECUTE FUNCTION prevent_raw_feed_mutation()""")


def downgrade():
    op.drop_index("ix_sectionals_runner_sequence", table_name="sectionals_200m")
    op.drop_table("sectionals_200m")
    op.drop_index("ix_runners_trainer_rb_dlr", table_name="runners")
    op.drop_index(op.f("ix_runners_trainer"), table_name="runners")
    op.drop_index(op.f("ix_runners_race_id"), table_name="runners")
    op.drop_index("ix_runners_closers", table_name="runners")
    op.drop_index("ix_runners_class_dropper", table_name="runners")
    op.drop_table("runners")
    op.drop_index(op.f("ix_races_meeting_id"), table_name="races")
    op.drop_table("races")
    op.drop_index("ix_meetings_track_date", table_name="meetings")
    op.drop_index("ix_meetings_state_date", table_name="meetings")
    op.drop_table("meetings")
    op.drop_index(op.f("ix_raw_feeds_sha256"), table_name="raw_feeds")
    op.drop_index("ix_raw_feeds_received_at", table_name="raw_feeds")
    op.drop_table("raw_feeds")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION prevent_raw_feed_mutation()")
