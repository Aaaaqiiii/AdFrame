from sqlalchemy.orm import Session

from sqlalchemy import func, select

from app.db.models import Asset, Shot, ShotEvidence, TimelineRevision, VideoAnalysis
from app.services.vision import AnalysisResult, align_vision_to_candidate_boundaries


def persist_vision_result(
    session: Session, project_id, job_id, raw_content: str, facts: AnalysisResult,
):
    """Persist source text separately from structured facts so it remains auditable."""
    record = VideoAnalysis(
        project_id=project_id, job_id=job_id, provider="comfly_gpt_frames",
        raw_content=raw_content, observations=facts.observations,
        inferences=facts.inferences, uncertainties=facts.uncertainties,
    )
    session.add(record)
    session.flush()
    if not facts.shots:
        session.commit()
        return record, None
    if abs(facts.shots[0].start_sec) > 0.001:
        raise ValueError("Vision timeline must start at zero")
    asset = session.scalar(
        select(Asset).where(Asset.project_id == project_id, Asset.kind == "reference_video").order_by(Asset.id.desc())
    )
    if asset is None or asset.duration_sec is None or abs(facts.shots[-1].end_sec - asset.duration_sec) > 0.05:
        raise ValueError("Vision timeline must cover the reference video")
    for previous, current in zip(facts.shots, facts.shots[1:]):
        if previous.end_sec <= previous.start_sec or abs(previous.end_sec - current.start_sec) > 0.001:
            raise ValueError("Vision timeline contains a gap, overlap, or invalid shot")
    version = (session.scalar(select(func.max(TimelineRevision.version)).where(TimelineRevision.project_id == project_id)) or 0) + 1
    candidate_revision = session.scalar(
        select(TimelineRevision)
        .where(TimelineRevision.project_id == project_id, TimelineRevision.source == "ffmpeg_candidates")
        .order_by(TimelineRevision.version.desc())
    )
    candidate_boundaries = [0.0, asset.duration_sec]
    if candidate_revision:
        candidate_shots = session.scalars(
            select(Shot).where(Shot.timeline_revision_id == candidate_revision.id).order_by(Shot.position)
        ).all()
        candidate_boundaries = [candidate_shots[0].start_sec, *(shot.end_sec for shot in candidate_shots)] if candidate_shots else candidate_boundaries
    facts = AnalysisResult(
        facts.summary, facts.observations, facts.inferences, facts.uncertainties,
        align_vision_to_candidate_boundaries(facts.shots, candidate_boundaries, asset.duration_sec),
    )
    revision = TimelineRevision(project_id=project_id, version=version, source="vision_hybrid")
    session.add(revision)
    session.flush()
    hybrid_shots = [Shot(
        timeline_revision_id=revision.id, position=index, start_sec=fact.start_sec, end_sec=fact.end_sec,
        people=fact.people, action=fact.action, product_interaction=fact.product_interaction,
        background=fact.background, camera=fact.camera, lighting=fact.lighting, on_screen_text=fact.on_screen_text,
        observations=fact.observations, inferences=fact.inferences, uncertainties=fact.uncertainties,
    ) for index, fact in enumerate(facts.shots)]
    session.add_all(hybrid_shots)
    session.flush()
    if candidate_revision:
        candidate_evidence = session.scalars(
            select(ShotEvidence)
            .join(Shot, ShotEvidence.shot_id == Shot.id)
            .where(Shot.timeline_revision_id == candidate_revision.id)
            .order_by(ShotEvidence.timestamp_sec)
        ).all()
        for evidence in candidate_evidence:
            target = next(
                (shot for shot in hybrid_shots if shot.start_sec <= evidence.timestamp_sec <= shot.end_sec),
                None,
            )
            if target:
                session.add(ShotEvidence(
                    shot_id=target.id,
                    timestamp_sec=evidence.timestamp_sec,
                    image_path=evidence.image_path,
                    source=evidence.source,
                    observation=evidence.observation,
                ))
    session.commit()
    return record, revision
