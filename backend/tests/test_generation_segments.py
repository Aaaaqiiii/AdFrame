import pytest

from app.services.generation_segments import SegmentDraft, SegmentValidationError, plan_segments, shot_slices_for_segment, validate_segments


def test_short_video_stays_one_segment():
    assert plan_segments(5.0, [("a", 0.0, 5.0)], 29.0, 8.0) == [
        SegmentDraft(0.0, 5.0, "video_edge", "video_edge", False)
    ]


def test_32_second_video_prefers_balanced_shot_boundary():
    shots = [("a", 0.0, 8.0), ("b", 8.0, 16.0), ("c", 16.0, 24.0), ("d", 24.0, 32.0)]
    assert [(s.start_sec, s.end_sec) for s in plan_segments(32.0, shots, 29.0, 8.0)] == [(0.0, 16.0), (16.0, 32.0)]


def test_75_second_video_uses_n_legal_segments():
    segments = plan_segments(75.0, [(str(i), i * 5.0, (i + 1) * 5.0) for i in range(15)], 29.0, 8.0)
    assert len(segments) == 3
    assert max(s.end_sec - s.start_sec for s in segments) <= 29.0


def test_single_long_shot_falls_back_to_inside_shot_boundaries():
    segments = plan_segments(61.0, [("a", 0.0, 61.0)], 29.0, 8.0)
    assert len(segments) == 3
    assert segments[0].end_boundary_type == "inside_shot"


def test_validation_rejects_gap():
    with pytest.raises(SegmentValidationError, match="空缺或重叠"):
        validate_segments(32.0, [SegmentDraft(0, 15, "video_edge", "shot_boundary", False), SegmentDraft(16, 32, "shot_boundary", "video_edge", False)], 29.0, 8.0)


def test_validation_rejects_overlap():
    with pytest.raises(SegmentValidationError, match="空缺或重叠"):
        validate_segments(32.0, [SegmentDraft(0, 17, "video_edge", "inside_shot", False), SegmentDraft(16, 32, "inside_shot", "video_edge", False)], 29.0, 8.0)


def test_validation_rejects_over_limit_with_configured_value():
    with pytest.raises(SegmentValidationError, match="超过 29 秒安全上限"):
        validate_segments(40.0, [SegmentDraft(0, 30, "video_edge", "inside_shot", False), SegmentDraft(30, 40, "inside_shot", "video_edge", False)], 29.0, 8.0)
    # 文案使用传入的 max_sec，而不是硬编码 29 秒。
    with pytest.raises(SegmentValidationError, match="超过 60 秒安全上限"):
        validate_segments(70.0, [SegmentDraft(0, 61, "video_edge", "inside_shot", False), SegmentDraft(61, 70, "inside_shot", "video_edge", False)], 60.0, 8.0)


def test_split_short_segment_requires_acceptance():
    # 拆分产生的短段（7 秒 < min_sec 8 秒）必须明确确认。
    segments = [SegmentDraft(0, 15, "video_edge", "shot_boundary", False), SegmentDraft(15, 22, "shot_boundary", "video_edge", False)]
    with pytest.raises(SegmentValidationError, match="短片段需要明确确认"):
        validate_segments(22.0, segments, 29.0, 8.0)
    # 补齐明确确认后通过校验。
    segments = [SegmentDraft(0, 15, "video_edge", "shot_boundary", False), SegmentDraft(15, 22, "shot_boundary", "video_edge", True)]
    validate_segments(22.0, segments, 29.0, 8.0)


def test_short_full_source_needs_no_acceptance():
    # 完整原视频短于 min_sec 时作为单段合法，无需确认。
    validate_segments(5.0, [SegmentDraft(0, 5, "video_edge", "video_edge", False)], 29.0, 8.0)


def test_shot_slices_use_segment_relative_time():
    slices = shot_slices_for_segment(SegmentDraft(16.0, 32.0, "shot_boundary", "video_edge", False), [("a", 0.0, 8.0), ("b", 8.0, 16.0), ("c", 16.0, 24.0), ("d", 24.0, 32.0)])
    assert slices == [
        {"shot_id": "c", "source_start_sec": 16.0, "source_end_sec": 24.0, "relative_start_sec": 0.0, "relative_end_sec": 8.0},
        {"shot_id": "d", "source_start_sec": 24.0, "source_end_sec": 32.0, "relative_start_sec": 8.0, "relative_end_sec": 16.0},
    ]
