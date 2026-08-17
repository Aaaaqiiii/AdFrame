"""AdFlow production schema baseline.

Full fixed snapshot of the pre-generation-closed-loop schema. Existing
installations stamped at this revision keep their tables; new databases
receive this exact structure through Alembic.
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_adflow_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False, server_default="preserve_product"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "timeline_revisions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", sa.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.UniqueConstraint("project_id", "version"),
    )
    op.create_table(
        "shots",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("timeline_revision_id", sa.UUID(as_uuid=True), sa.ForeignKey("timeline_revisions.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("start_sec", sa.Float(), nullable=False),
        sa.Column("end_sec", sa.Float(), nullable=False),
        sa.Column("people", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("product", sa.Text(), nullable=True),
        sa.Column("product_interaction", sa.Text(), nullable=True),
        sa.Column("background", sa.Text(), nullable=True),
        sa.Column("camera", sa.Text(), nullable=True),
        sa.Column("lighting", sa.Text(), nullable=True),
        sa.Column("visual_style", sa.Text(), nullable=True),
        sa.Column("keep_unchanged", sa.Text(), nullable=True),
        sa.Column("on_screen_text", sa.Text(), nullable=True),
        sa.Column("observations", sa.Text(), nullable=True),
        sa.Column("inferences", sa.Text(), nullable=True),
        sa.Column("uncertainties", sa.Text(), nullable=True),
        sa.Column("analysis_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("analysis_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("timeline_revision_id", "position"),
        sa.CheckConstraint("start_sec >= 0", name="ck_shots_non_negative_start"),
        sa.CheckConstraint("end_sec > start_sec", name="ck_shots_positive_range"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", sa.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("shot_id", sa.UUID(as_uuid=True), sa.ForeignKey("shots.id"), nullable=True),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("external_task_id", sa.String(length=255), nullable=True),
        sa.Column("provider_input_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("attempts >= 0", name="ck_jobs_attempts_non_negative"),
    )
    op.create_table(
        "video_analyses",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", sa.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("job_id", sa.UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("shot_id", sa.UUID(as_uuid=True), sa.ForeignKey("shots.id"), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("observations", sa.Text(), nullable=True),
        sa.Column("inferences", sa.Text(), nullable=True),
        sa.Column("uncertainties", sa.Text(), nullable=True),
    )
    op.create_table(
        "shot_evidence",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("shot_id", sa.UUID(as_uuid=True), sa.ForeignKey("shots.id"), nullable=False),
        sa.Column("timestamp_sec", sa.Float(), nullable=False),
        sa.Column("image_path", sa.String(length=500), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="ffmpeg"),
        sa.Column("observation", sa.Text(), nullable=True),
    )
    op.create_table(
        "shot_ai_summaries",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("shot_id", sa.UUID(as_uuid=True), sa.ForeignKey("shots.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("shot_id", "version"),
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", sa.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("original_path", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("fps", sa.Float(), nullable=True),
        sa.Column("public_url", sa.String(length=1000), nullable=True),
        sa.Column("public_url_expires_at", sa.String(length=40), nullable=True),
        sa.Column("profile_text", sa.Text(), nullable=True),
        sa.Column("profile_json", sa.Text(), nullable=True),
        sa.Column("profile_user_edited", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("analysis_status", sa.String(length=20), nullable=True),
        sa.Column("analysis_error", sa.Text(), nullable=True),
    )
    op.create_table(
        "shot_edits",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", sa.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("shot_id", sa.UUID(as_uuid=True), sa.ForeignKey("shots.id"), nullable=False),
        sa.Column("people", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("product", sa.Text(), nullable=True),
        sa.Column("product_interaction", sa.Text(), nullable=True),
        sa.Column("background", sa.Text(), nullable=True),
        sa.Column("camera", sa.Text(), nullable=True),
        sa.Column("lighting", sa.Text(), nullable=True),
        sa.Column("visual_style", sa.Text(), nullable=True),
        sa.Column("visible_text", sa.Text(), nullable=True),
        sa.Column("uncertainties", sa.Text(), nullable=True),
        sa.Column("keep_unchanged", sa.Text(), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("ai_summary_version", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("project_id", "shot_id"),
    )
    op.create_table(
        "prompt_revisions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", sa.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("visual_direction", sa.Text(), nullable=False, server_default=""),
        sa.Column("audio_mode", sa.String(length=30), nullable=False, server_default="keep_original"),
        sa.Column("audio_style", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("replace_product", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("replace_person", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source_timeline_revision_id", sa.UUID(as_uuid=True), sa.ForeignKey("timeline_revisions.id"), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="completed"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("project_id", "version"),
    )
    op.create_table(
        "generations",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", sa.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default="volcengine"),
        sa.Column("ratio", sa.String(length=10), nullable=False, server_default="9:16"),
        sa.Column("duration", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("generate_audio", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reference_image_urls", sa.Text(), nullable=True),
        sa.Column("external_task_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("result_url", sa.String(length=1000), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("result_path", sa.String(length=500), nullable=True),
        sa.UniqueConstraint("project_id", "version"),
        sa.CheckConstraint("attempts >= 0", name="ck_generations_attempts_non_negative"),
    )


def downgrade() -> None:
    op.drop_table("generations")
    op.drop_table("prompt_revisions")
    op.drop_table("shot_edits")
    op.drop_table("assets")
    op.drop_table("shot_ai_summaries")
    op.drop_table("shot_evidence")
    op.drop_table("video_analyses")
    op.drop_table("jobs")
    op.drop_table("shots")
    op.drop_table("timeline_revisions")
    op.drop_table("projects")
