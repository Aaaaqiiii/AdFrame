"""Add nullable batch identity to generations.

Additive upgrade preserving all existing rows. Batch rows share one batch UUID
and carry a creation-time position/size; retries reuse the same position with a
newer project-wide version, so no uniqueness constraint is added.
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_generation_batches"
down_revision = "0005_segment_generations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generations", sa.Column("generation_batch_id", sa.Uuid(), nullable=True))
    op.add_column("generations", sa.Column("batch_position", sa.Integer(), nullable=True))
    op.add_column("generations", sa.Column("batch_size", sa.Integer(), nullable=True))
    op.create_index("ix_generations_project_batch", "generations", ["project_id", "generation_batch_id"])
    op.create_index("ix_generations_batch_position", "generations", ["generation_batch_id", "batch_position"])


def downgrade() -> None:
    op.drop_index("ix_generations_batch_position", table_name="generations")
    op.drop_index("ix_generations_project_batch", table_name="generations")
    op.drop_column("generations", "batch_size")
    op.drop_column("generations", "batch_position")
    op.drop_column("generations", "generation_batch_id")
