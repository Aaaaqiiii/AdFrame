"""Add prompt mode and generation-segment linkage to prompt revisions.

Additive upgrade preserving all existing rows. Legacy rows default to
``full_video_description``; the new workflow uses ``reference_video_edit`` with
an optional ``generation_segment_id``.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_segment_edit_prompts"
down_revision = "0003_reference_video_segments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prompt_revisions", sa.Column("prompt_mode", sa.String(30), nullable=False, server_default="full_video_description"))
    op.add_column("prompt_revisions", sa.Column("generation_segment_id", sa.Uuid(), sa.ForeignKey("generation_segments.id"), nullable=True))
    op.create_index("ix_prompt_revisions_segment", "prompt_revisions", ["generation_segment_id"])


def downgrade() -> None:
    op.drop_index("ix_prompt_revisions_segment", table_name="prompt_revisions")
    op.drop_column("prompt_revisions", "generation_segment_id")
    op.drop_column("prompt_revisions", "prompt_mode")
