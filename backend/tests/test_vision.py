import json
from pathlib import Path

from app.services.vision import AnalysisResult, AnalyzedShot, align_vision_to_candidate_boundaries, build_fact_timeline, parse_vision_facts


class FakeVisionProvider:
    def analyze_video(self, video_url: str) -> AnalysisResult:
        assert video_url == "https://example.test/reference.mp4"
        return AnalysisResult(
            summary="产品展示",
            observations="人物手持产品",
            inferences="强调使用场景",
            uncertainties="标签文字不可辨认",
            shots=[
                AnalyzedShot(0, 2, people="手部", action="拿起", product_interaction="手持产品"),
                AnalyzedShot(2, 5, people="手部", action="展示", product_interaction="旋转产品"),
            ],
        )


def test_video_analysis_preserves_facts_and_candidate_boundaries() -> None:
    result = build_fact_timeline(
        FakeVisionProvider(),
        "https://example.test/reference.mp4",
        [0.0, 2.1, 5.0],
    )

    assert result.summary == "产品展示"
    assert result.shots[0].observations is None
    assert result.shots[0].people == "手部"
    assert result.shots[1].start_sec == 2.1


def test_unstructured_vision_content_is_not_guessed_into_shot_facts() -> None:
    result = parse_vision_facts("Someone holds the product.")

    assert result.shots == []
    assert result.uncertainties is not None


def test_parse_vision_facts_serializes_structured_fields_as_readable_text() -> None:
    result = parse_vision_facts(json.dumps({
        "summary": "ok",
        "shots": [{
            "start_sec": 0,
            "end_sec": 1,
            "people": [{"description": "一名人物"}],
            "action": {"continuous_action": "抬起瓶子"},
            "observations": ["瓶身可见"],
        }],
    }, ensure_ascii=False))

    assert result.shots[0].people == '[{"description":"一名人物"}]'
    assert result.shots[0].action == '{"continuous_action":"抬起瓶子"}'
    assert result.shots[0].observations == '["瓶身可见"]'


def test_structured_vision_content_keeps_explicit_shot_facts() -> None:
    result = parse_vision_facts('{"summary":"demo","shots":[{"start_sec":0,"end_sec":2,"action":"opens box"}]}')

    assert result.summary == "demo"
    assert result.shots[0].action == "opens box"


def test_vision_semantic_boundaries_snap_to_ffmpeg_candidates() -> None:
    aligned = align_vision_to_candidate_boundaries(
        [AnalyzedShot(0, 2.08, action="pick up"), AnalyzedShot(2.08, 5, action="show")],
        [0, 2.1, 3.8, 5],
        5,
    )

    assert [(shot.start_sec, shot.end_sec) for shot in aligned] == [(0, 2.1), (2.1, 5)]


def test_vision_semantic_ranges_merge_ffmpeg_atoms_without_inventing_cut_points() -> None:
    aligned = align_vision_to_candidate_boundaries(
        [AnalyzedShot(0, 3.7, action="one continuous move"), AnalyzedShot(3.7, 5, action="close up")],
        [0, 1.2, 2.1, 3.8, 5],
        5,
    )

    assert [(shot.start_sec, shot.end_sec) for shot in aligned] == [(0, 3.8), (3.8, 5)]
