from __future__ import annotations

from uuid import UUID, uuid4
from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(30), default="preserve_product")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    generations: Mapped[list[Generation]] = relationship(back_populates="project")
    timeline_revisions: Mapped[list[TimelineRevision]] = relationship(back_populates="project")
    assets: Mapped[list[Asset]] = relationship(back_populates="project")
    jobs: Mapped[list[Job]] = relationship(back_populates="project")
    prompt_revisions: Mapped[list[PromptRevision]] = relationship(back_populates="project")
    video_analyses: Mapped[list[VideoAnalysis]] = relationship(back_populates="project")
    shot_edits: Mapped[list[ShotEdit]] = relationship(back_populates="project")


class Generation(Base):
    __tablename__ = "generations"
    __table_args__ = (
        UniqueConstraint("project_id", "version"),
        Index("ix_generations_project_status", "project_id", "status"),
        Index("ix_generations_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_generations_submission_fingerprint", "submission_fingerprint"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    version: Mapped[int] = mapped_column(Integer)
    prompt_version: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(50), default="volcengine")
    ratio: Mapped[str] = mapped_column(String(10), default="9:16")
    duration: Mapped[int] = mapped_column(Integer, default=5)
    generate_audio: Mapped[bool] = mapped_column(default=False)
    reference_image_urls: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    result_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)
    leased_at: Mapped[datetime | None] = mapped_column(nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    result_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    request_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_asset_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_segment_id: Mapped[UUID | None] = mapped_column(ForeignKey("generation_segments.id"), nullable=True)
    provider_response_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    submission_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    project: Mapped[Project] = relationship(back_populates="generations")


class GenerationSegment(Base):
    __tablename__ = "generation_segments"
    __table_args__ = (
        UniqueConstraint("project_id", "plan_version", "position"),
        Index("ix_generation_segments_project_plan", "project_id", "plan_version"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    plan_version: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    source_start_sec: Mapped[float] = mapped_column(Float)
    source_end_sec: Mapped[float] = mapped_column(Float)
    start_boundary_type: Mapped[str] = mapped_column(String(20))
    end_boundary_type: Mapped[str] = mapped_column(String(20))
    source_timeline_revision_id: Mapped[UUID] = mapped_column(ForeignKey("timeline_revisions.id"))
    short_segment_accepted: Mapped[bool] = mapped_column(default=False)
    clip_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    public_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    public_url_expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class PromptRevision(Base):
    __tablename__ = "prompt_revisions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    version: Mapped[int] = mapped_column(Integer)
    prompt_mode: Mapped[str] = mapped_column(String(30), default="full_video_description")
    generation_segment_id: Mapped[UUID | None] = mapped_column(ForeignKey("generation_segments.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    visual_direction: Mapped[str] = mapped_column(Text, default="")
    audio_mode: Mapped[str] = mapped_column(String(30), default="keep_original")
    audio_style: Mapped[str] = mapped_column(String(500), default="")
    replace_product: Mapped[bool] = mapped_column(default=False)
    replace_person: Mapped[bool] = mapped_column(default=False)
    source_timeline_revision_id: Mapped[UUID | None] = mapped_column(ForeignKey("timeline_revisions.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    project: Mapped[Project] = relationship(back_populates="prompt_revisions")


class VideoAnalysis(Base):
    __tablename__ = "video_analyses"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    # 逐镜双模型的中间结果只在后台保存，不直接暴露给前端。
    shot_id: Mapped[UUID | None] = mapped_column(ForeignKey("shots.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(50))
    raw_content: Mapped[str] = mapped_column(Text)
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)
    inferences: Mapped[str | None] = mapped_column(Text, nullable=True)
    uncertainties: Mapped[str | None] = mapped_column(Text, nullable=True)
    project: Mapped[Project] = relationship(back_populates="video_analyses")


class ShotEdit(Base):
    __tablename__ = "shot_edits"
    __table_args__ = (UniqueConstraint("project_id", "shot_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    shot_id: Mapped[UUID] = mapped_column(ForeignKey("shots.id"))
    people: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    product: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_interaction: Mapped[str | None] = mapped_column(Text, nullable=True)
    background: Mapped[str | None] = mapped_column(Text, nullable=True)
    camera: Mapped[str | None] = mapped_column(Text, nullable=True)
    lighting: Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    visible_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    uncertainties: Mapped[str | None] = mapped_column(Text, nullable=True)
    keep_unchanged: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    ai_summary_version: Mapped[int] = mapped_column(Integer, default=0)
    project: Mapped[Project] = relationship(back_populates="shot_edits")


class TimelineRevision(Base):
    __tablename__ = "timeline_revisions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))
    project: Mapped[Project] = relationship(back_populates="timeline_revisions")
    shots: Mapped[list[Shot]] = relationship(back_populates="timeline_revision")


class Shot(Base):
    __tablename__ = "shots"
    __table_args__ = (UniqueConstraint("timeline_revision_id", "position"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    timeline_revision_id: Mapped[UUID] = mapped_column(ForeignKey("timeline_revisions.id"))
    position: Mapped[int] = mapped_column(Integer)
    start_sec: Mapped[float] = mapped_column(Float)
    end_sec: Mapped[float] = mapped_column(Float)
    people: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    product: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_interaction: Mapped[str | None] = mapped_column(Text, nullable=True)
    background: Mapped[str | None] = mapped_column(Text, nullable=True)
    camera: Mapped[str | None] = mapped_column(Text, nullable=True)
    lighting: Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    keep_unchanged: Mapped[str | None] = mapped_column(Text, nullable=True)
    on_screen_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)
    inferences: Mapped[str | None] = mapped_column(Text, nullable=True)
    uncertainties: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_status: Mapped[str] = mapped_column(String(20), default="pending")
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeline_revision: Mapped[TimelineRevision] = relationship(back_populates="shots")
    evidence: Mapped[list[ShotEvidence]] = relationship(back_populates="shot")
    ai_summaries: Mapped[list[ShotAISummary]] = relationship(back_populates="shot")


class ShotAISummary(Base):
    __tablename__ = "shot_ai_summaries"
    __table_args__ = (UniqueConstraint("shot_id", "version"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    shot_id: Mapped[UUID] = mapped_column(ForeignKey("shots.id"))
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    shot: Mapped[Shot] = relationship(back_populates="ai_summaries")


class ShotEvidence(Base):
    __tablename__ = "shot_evidence"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    shot_id: Mapped[UUID] = mapped_column(ForeignKey("shots.id"))
    timestamp_sec: Mapped[float] = mapped_column(Float)
    image_path: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(30), default="ffmpeg")
    observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    shot: Mapped[Shot] = relationship(back_populates="evidence")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(50))
    original_path: Mapped[str] = mapped_column(String(500))
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    public_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    public_url_expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    profile_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_user_edited: Mapped[bool] = mapped_column(default=False)
    analysis_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    project: Mapped[Project] = relationship(back_populates="assets")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    shot_id: Mapped[UUID | None] = mapped_column(ForeignKey("shots.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_input_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)
    leased_at: Mapped[datetime | None] = mapped_column(nullable=True)
    leased_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    project: Mapped[Project] = relationship(back_populates="jobs")
