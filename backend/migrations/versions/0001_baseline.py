"""Baseline schema (SPEC §11).

Revision ID: 0001_baseline
Revises:
"""

from alembic import op

from datapilot.db.schema import metadata

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    metadata.drop_all(op.get_bind())
