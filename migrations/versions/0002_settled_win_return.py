"""Retain published tote payouts separately from closing decimal odds.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    # A column-level CHECK can be added atomically on both PostgreSQL and SQLite,
    # without rebuilding an existing runners table referenced by sectionals.
    op.add_column(
        "runners",
        sa.Column(
            "settled_win_return",
            sa.Float(),
            sa.CheckConstraint(
                "settled_win_return IS NULL OR settled_win_return >= 0",
                name="ck_runner_settled_return",
            ),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("runners", "settled_win_return")
