"""Freeze prompt inputs and keep refinement instructions separate."""
from alembic import op
import sqlalchemy as sa

revision = "0007_prompt_input_snapshots"
down_revision = "0006_generation_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prompt_revisions", sa.Column("operation_instruction", sa.Text(), nullable=False, server_default=""))
    op.add_column("prompt_revisions", sa.Column("reference_asset_ids", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("prompt_revisions", "reference_asset_ids")
    op.drop_column("prompt_revisions", "operation_instruction")
