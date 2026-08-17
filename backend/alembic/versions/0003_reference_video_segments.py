"""Add immutable generation segment plans.

Additive upgrade preserving all existing rows and tables. Segment bounds,
position, plan version, boundary types, and timeline revision are immutable;
transport metadata (clip path, public URL) is mutable and starts empty.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_reference_video_segments"
down_revision = "0002_generation_closed_loop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_segments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_start_sec", sa.Float(), nullable=False),
        sa.Column("source_end_sec", sa.Float(), nullable=False),
        sa.Column("start_boundary_type", sa.String(20), nullable=False),
        sa.Column("end_boundary_type", sa.String(20), nullable=False),
        sa.Column("source_timeline_revision_id", sa.Uuid(), sa.ForeignKey("timeline_revisions.id"), nullable=False),
        sa.Column("short_segment_accepted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("clip_path", sa.String(500), nullable=True),
        sa.Column("public_url", sa.String(1000), nullable=True),
        sa.Column("public_url_expires_at", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "plan_version", "position"),
    )
    op.create_index("ix_generation_segments_project_plan", "generation_segments", ["project_id", "plan_version"])


def downgrade() -> None:
    op.drop_index("ix_generation_segments_project_plan", table_name="generation_segments")
    op.drop_table("generation_segments")
