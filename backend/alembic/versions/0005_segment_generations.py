"""Link generations to a generation segment.

Additive upgrade preserving all existing rows. Historical generations remain
nullable; new segmented submissions reference one generation segment.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_segment_generations"
down_revision = "0004_segment_edit_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generations", sa.Column("generation_segment_id", sa.Uuid(), sa.ForeignKey("generation_segments.id"), nullable=True))
    op.create_index("ix_generations_segment", "generations", ["generation_segment_id"])


def downgrade() -> None:
    op.drop_index("ix_generations_segment", table_name="generations")
    op.drop_column("generations", "generation_segment_id")
