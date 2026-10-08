"""User-uploaded datasets ("Your data").

Revision ID: 0002_user_datasets
Revises: 0001_baseline
"""

from alembic import op

from datapilot.db.schema import user_datasets

revision = "0002_user_datasets"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    user_datasets.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    user_datasets.drop(op.get_bind(), checkfirst=True)
