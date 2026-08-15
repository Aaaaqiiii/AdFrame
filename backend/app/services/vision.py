from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol


@dataclass(frozen=True)
class AnalyzedShot:
    start_sec: float
    end_sec: float
    people: str | None = None
    action: str | None = None
    product_interaction: str | None = None
    background: str | None = None
    camera: str | None = None
    lighting: str | None = None
    on_screen_text: str | None = None
    observations: str | None = None
    inferences: str | None = None
    uncertainties: str | None = None


@dataclass(frozen=True)
class AnalysisResult:
    summary: str
    observations: str | None
    inferences: str | None
    uncertainties: str | None
    shots: list[AnalyzedShot]


class VisionProvider(Protocol):
    def analyze_video(self, video_url: str) -> AnalysisResult: ...


def _fact_text(value) -> str | None:
    """Keep provider JSON flexible while storing facts in readable text columns."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def align_vision_to_candidate_boundaries(shots: list[AnalyzedShot], candidates: list[float], duration_sec: float) -> list[AnalyzedShot]:
    """Snap semantic segments to real candidate cuts; Vision may merge atoms but never invent cuts."""
    if not shots:
        return [AnalyzedShot(start, end) for start, end in zip(candidates, candidates[1:])]
    bounds = sorted({round(value, 3) for value in [0.0, *candidates, duration_sec]})
    def snap(value: float) -> float:
        return min(bounds, key=lambda candidate: abs(candidate - value))
    aligned: list[AnalyzedShot] = []
    for index, shot in enumerate(shots):
        start = 0.0 if index == 0 else snap(shot.start_sec)
        end = duration_sec if index == len(shots) - 1 else snap(shot.end_sec)
        if end <= start:
            continue
        aligned.append(AnalyzedShot(start, end, shot.people, shot.action, shot.product_interaction, shot.background, shot.camera, shot.lighting, shot.on_screen_text, shot.observations, shot.inferences, shot.uncertainties))
    return aligned or [AnalyzedShot(start, end) for start, end in zip(bounds, bounds[1:])]


def build_fact_timeline(provider: VisionProvider, video_url: str, candidate_boundaries: list[float]) -> AnalysisResult:
    """Use AI semantic ranges when supplied; FFmpeg candidates only constrain coverage."""
    result = provider.analyze_video(video_url)
    if not result.shots:
        return AnalysisResult(result.summary, result.observations, result.inferences, result.uncertainties, [
            AnalyzedShot(start, end) for start, end in zip(candidate_boundaries, candidate_boundaries[1:])
        ])
    return AnalysisResult(result.summary, result.observations, result.inferences, result.uncertainties, align_vision_to_candidate_boundaries(result.shots, candidate_boundaries, candidate_boundaries[-1]))


def parse_vision_facts(content: str) -> AnalysisResult:
    """Accept an explicitly structured Vision response; never infer facts from prose."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return AnalysisResult(
            summary=content,
            observations=content,
            inferences=None,
            uncertainties="The provider returned unstructured content; review it before using it as shot facts.",
            shots=[],
        )
    shots = [
        AnalyzedShot(
            start_sec=float(item["start_sec"]), end_sec=float(item["end_sec"]), people=_fact_text(item.get("people")),
            action=_fact_text(item.get("action")), product_interaction=_fact_text(item.get("product_interaction")),
            background=_fact_text(item.get("background")), camera=_fact_text(item.get("camera")),
            lighting=_fact_text(item.get("lighting")), on_screen_text=_fact_text(item.get("on_screen_text")),
            observations=_fact_text(item.get("observations")), inferences=_fact_text(item.get("inferences")),
            uncertainties=_fact_text(item.get("uncertainties")),
        ) for item in data.get("shots", [])
    ]
    return AnalysisResult(
        summary=_fact_text(data.get("summary")) or content, observations=_fact_text(data.get("observations")),
        inferences=_fact_text(data.get("inferences")), uncertainties=_fact_text(data.get("uncertainties")), shots=shots,
    )
