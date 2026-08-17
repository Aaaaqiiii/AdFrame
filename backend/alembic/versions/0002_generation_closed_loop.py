"""Add generation closed-loop recovery metadata.

Additive upgrade preserving all existing rows and tables.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_generation_closed_loop"
down_revision = "0001_adflow_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generations", sa.Column("request_snapshot", sa.Text(), nullable=True))
    op.add_column("generations", sa.Column("reference_asset_ids", sa.Text(), nullable=True))
    op.add_column("generations", sa.Column("provider_response_summary", sa.Text(), nullable=True))
    op.add_column("generations", sa.Column("submission_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("generations", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_generations_project_status", "generations", ["project_id", "status"])
    op.create_index("ix_generations_status_next_attempt", "generations", ["status", "next_attempt_at"])
    op.create_index("ix_generations_submission_fingerprint", "generations", ["submission_fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_generations_submission_fingerprint", table_name="generations")
    op.drop_index("ix_generations_status_next_attempt", table_name="generations")
    op.drop_index("ix_generations_project_status", table_name="generations")
    op.drop_column("generations", "completed_at")
    op.drop_column("generations", "submission_fingerprint")
    op.drop_column("generations", "provider_response_summary")
    op.drop_column("generations", "reference_asset_ids")
    op.drop_column("generations", "request_snapshot")
