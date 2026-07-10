"""Add Anthropic 1P model id to model catalog for fallback routing."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003_anthropic_model_id"
down_revision = "002_bedrock_region"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_catalog", sa.Column("anthropic_model_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_catalog", "anthropic_model_id")
