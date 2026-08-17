"""Pure generation-segment planning and validation.

This module has no database session dependency: it only consumes numeric video
duration and ordered ``(shot_id, start_sec, end_sec)`` values, and returns
immutable segment drafts plus relative-time shot slices.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil

EPSILON = 0.001


class SegmentValidationError(ValueError):
    """Raised when a segment plan violates the coverage or duration rules."""


@dataclass(frozen=True)
class SegmentDraft:
    start_sec: float
    end_sec: float
    start_boundary_type: str
    end_boundary_type: str
    short_segment_accepted: bool = False


def _boundaries(shots: list[tuple[str, float, float]], duration_sec: float) -> list[float]:
    """Sorted unique candidate boundaries: 0, every shot end, and duration."""
    values = [0.0, duration_sec]
    values.extend(end for _, _, end in shots)
    values = sorted({round(value, 6) for value in values})
    # 过滤出严格位于 (0, duration) 内的内部边界，保留首尾端点。
    return [value for value in values if value >= -EPSILON and value <= duration_sec + EPSILON]


def plan_segments(
    duration_sec: float,
    shots: list[tuple[str, float, float]],
    max_sec: float,
    min_sec: float,
) -> list[SegmentDraft]:
    """Plan the fewest legal segments, preferring shot boundaries.

    A single segment covering the whole source is always legal (the whole video
    may be shorter than ``min_sec``). When no all-shot-boundary plan exists,
    fall back to equal-width ranges with ``inside_shot`` internal boundaries.
    """
    if duration_sec <= max_sec + EPSILON:
        result = [SegmentDraft(0.0, duration_sec, "video_edge", "video_edge", False)]
        validate_segments(duration_sec, result, max_sec, min_sec)
        return result

    candidates = _boundaries(shots, duration_sec)
    minimum_count = max(2, ceil(duration_sec / max_sec))
    # 动态规划：在候选边界中找恰好 count 段的最均衡合法方案。
    for count in range(minimum_count, len(candidates)):
        path = _balanced_shot_boundary_plan(candidates, duration_sec, count, max_sec, min_sec)
        if path is not None:
            # 首段起点是 video_edge；末段终点是 video_edge；内部切点都是 shot_boundary。
            drafts: list[SegmentDraft] = []
            for index, (start, end) in enumerate(path):
                start_type = "video_edge" if start <= EPSILON else "shot_boundary"
                end_type = "video_edge" if end >= duration_sec - EPSILON else "shot_boundary"
                drafts.append(SegmentDraft(start, end, start_type, end_type, False))
            validate_segments(duration_sec, drafts, max_sec, min_sec)
            return drafts

    # 无合法镜头边界方案：按最小段数等宽切分，内部边界标记 inside_shot。
    equal = []
    for index in range(minimum_count):
        start = duration_sec * index / minimum_count
        end = duration_sec * (index + 1) / minimum_count
        start_type = "video_edge" if index == 0 else "inside_shot"
        end_type = "video_edge" if index == minimum_count - 1 else "inside_shot"
        equal.append(SegmentDraft(start, end, start_type, end_type, False))
    validate_segments(duration_sec, equal, max_sec, min_sec)
    return equal


def _balanced_shot_boundary_plan(
    candidates: list[float],
    duration_sec: float,
    count: int,
    max_sec: float,
    min_sec: float,
) -> list[tuple[float, float]] | None:
    """Find exactly ``count`` consecutive legal segments using DP.

    A transition ``previous_boundary -> boundary`` is legal when its duration is
    within ``[min_sec, max_sec]`` (the whole source shorter than ``min_sec`` is
    already handled by the single-segment branch). Cost is squared deviation
    from the equal share ``duration_sec / count``; the minimum-cost complete
    path reaching ``duration_sec`` is returned.
    """
    target = duration_sec
    best = {0.0: (0.0, [])}  # boundary -> (cost, path of (start, end) tuples)
    for _ in range(count):
        next_best: dict[float, tuple[float, list[tuple[float, float]]]] = {}
        for boundary in candidates:
            if boundary <= EPSILON:
                continue
            options: list[tuple[float, list[tuple[float, float]]]] = []
            for previous in best:
                duration = boundary - previous
                if duration < min_sec - EPSILON or duration > max_sec + EPSILON:
                    continue
                if previous <= EPSILON and duration <= max_sec + EPSILON:
                    pass
                cost = best[previous][0] + (duration - target / count) ** 2
                options.append((cost, best[previous][1] + [(previous, boundary)]))
            if options:
                next_best[boundary] = min(options, key=lambda item: item[0])
        best = next_best
        if not best:
            return None
    if duration_sec in best:
        return best[duration_sec][1]
    # 容差内接受以 duration 为终点的路径。
    close = [value for value in best if abs(value - duration_sec) <= EPSILON]
    if close:
        return best[close[0]][1]
    return None


def validate_segments(
    duration_sec: float,
    segments: list[SegmentDraft],
    max_sec: float,
    min_sec: float,
) -> None:
    if not segments:
        raise SegmentValidationError("生成片段不能为空")
    if abs(segments[0].start_sec) > EPSILON:
        raise SegmentValidationError("生成片段必须从 0 开始")
    for index, segment in enumerate(segments):
        if segment.end_sec <= segment.start_sec:
            raise SegmentValidationError("生成片段结束时间必须大于开始时间")
        if segment.end_sec - segment.start_sec > max_sec + EPSILON:
            raise SegmentValidationError("生成片段超过 29 秒安全上限")
        if index:
            previous = segments[index - 1]
            if abs(segment.start_sec - previous.end_sec) > EPSILON:
                raise SegmentValidationError("生成片段不能有空缺或重叠")
        if index == len(segments) - 1 and abs(segment.end_sec - duration_sec) > EPSILON:
            raise SegmentValidationError("生成片段必须覆盖完整视频")
    for segment in segments:
        # 整个源视频短于 min_sec 时作为单段合法，无需额外确认。
        covers_full_source = abs(segment.start_sec) <= EPSILON and abs(segment.end_sec - duration_sec) <= EPSILON
        if segment.end_sec - segment.start_sec < min_sec - EPSILON and not segment.short_segment_accepted and not covers_full_source:
            raise SegmentValidationError("短片段需要明确确认")


def shot_slices_for_segment(
    segment: SegmentDraft,
    shots: list[tuple[str, float, float]],
) -> list[dict[str, float | str]]:
    """Return only positive intersections of the segment with each shot."""
    slices: list[dict[str, float | str]] = []
    for shot_id, start_sec, end_sec in shots:
        overlap_start = max(start_sec, segment.start_sec)
        overlap_end = min(end_sec, segment.end_sec)
        if overlap_end - overlap_start <= EPSILON:
            continue
        slices.append({
            "shot_id": shot_id,
            "source_start_sec": overlap_start,
            "source_end_sec": overlap_end,
            "relative_start_sec": overlap_start - segment.start_sec,
            "relative_end_sec": overlap_end - segment.start_sec,
        })
    return slices
