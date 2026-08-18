import pytest

from app.services.full_prompt import (
    FullPromptDocument,
    FullPromptValidationError,
    PromptBlock,
    derive_segment_prompt,
    expected_full_prompt_labels,
    format_time_label,
    parse_full_prompt,
    render_full_prompt,
    validate_full_prompt,
)


def prompt_text() -> str:
    return (
        "全局规则：原参考视频是时间轴、动作、构图、运镜、节奏和镜头顺序的最高优先级参考。\n"
        "产品参考图用途：锁定正面、侧面结构。\n"
        "目标产品档案：已确认目标产品。\n\n"
        "00:00.00–00:04.00\n"
        "保持：人物身份、动作节奏、手部位置、背景、构图、镜头运动不变。\n"
        "修改：无。\n"
        "删除：无。\n"
        "禁止：不得新增文字或改变动作。\n\n"
        "00:04.00–00:09.50\n"
        "保持：镜头二保持正文。\n"
        "修改：无。\n"
        "删除：无。\n"
        "禁止：不得新增文字。"
    )


def shot_ranges() -> list[tuple[float, float]]:
    return [(0.0, 4.0), (4.0, 9.5)]


def three_shot_prompt() -> str:
    return (
        "全局规则：原参考视频是最高优先级参考。\n"
        "目标产品档案：已确认目标产品。\n\n"
        "00:00.00–00:10.00\n"
        "保持：镜头一保持正文。\n"
        "修改：无。\n"
        "删除：无。\n"
        "禁止：无。\n\n"
        "00:10.00–00:25.00\n"
        "保持：镜头二保持正文。\n"
        "修改：无。\n"
        "删除：无。\n"
        "禁止：无。\n\n"
        "00:25.00–00:40.00\n"
        "保持：镜头三保持正文。\n"
        "修改：无。\n"
        "删除：无。\n"
        "禁止：无。"
    )


def test_full_prompt_accepts_every_absolute_shot_block_once():
    document = validate_full_prompt(
        prompt_text(),
        [(0.0, 4.0), (4.0, 9.5)],
        required_prefixes=("产品参考图用途", "目标产品档案"),
    )
    assert [(b.source_start_sec, b.source_end_sec) for b in document.blocks] == [
        (0.0, 4.0),
        (4.0, 9.5),
    ]


@pytest.mark.parametrize(
    "mutator, expected",
    [
        (lambda text: text.replace("00:04.00–00:09.50", "00:04.00–00:09.40"), "时间块"),
        (lambda text: text.replace("删除：无", ""), "删除："),
        (lambda text: text + "\n\n00:00.00–00:04.00\n保持：重复\n修改：无\n删除：无\n禁止：无", "重复"),
        (lambda text: text.replace("产品参考图用途", "参考资料"), "产品参考图用途"),
    ],
)
def test_full_prompt_rejects_missing_changed_or_duplicate_structure(mutator, expected):
    with pytest.raises(FullPromptValidationError, match=expected):
        validate_full_prompt(
            mutator(prompt_text()),
            [(0.0, 4.0), (4.0, 9.5)],
            required_prefixes=("产品参考图用途",),
        )


def test_format_time_label_beyond_59_seconds():
    assert format_time_label(0.0, 65.0) == "00:00.00–01:05.00"
    assert format_time_label(125.5, 190.25) == "02:05.50–03:10.25"


def test_crlf_and_blank_lines_are_normalized():
    text = "全局规则：前缀。\r\n\r\n00:00.00–00:04.00\r\n保持：a\r\n修改：无\r\n删除：无\r\n禁止：无\r\n"
    document = validate_full_prompt(text, [(0.0, 4.0)])
    assert [b.source_start_sec for b in document.blocks] == [0.0]
    assert document.global_prefix == "全局规则：前缀。"


def test_out_of_order_blocks_are_rejected():
    text = (
        "全局规则：前缀。\n\n"
        "00:04.00–00:09.50\n保持：b\n修改：无\n删除：无\n禁止：无\n\n"
        "00:00.00–00:04.00\n保持：a\n修改：无\n删除：无\n禁止：无"
    )
    with pytest.raises(FullPromptValidationError, match="顺序"):
        validate_full_prompt(text, [(0.0, 4.0), (4.0, 9.5)])


def test_unknown_extra_blocks_are_rejected():
    text = prompt_text() + "\n\n00:09.50–00:12.00\n保持：c\n修改：无\n删除：无\n禁止：无"
    with pytest.raises(FullPromptValidationError, match="未知|多余"):
        validate_full_prompt(text, [(0.0, 4.0), (4.0, 9.5)])


def test_empty_column_body_is_rejected():
    text = (
        "全局规则：前缀。\n\n"
        "00:00.00–00:04.00\n保持：a\n修改：\n删除：无\n禁止：无"
    )
    with pytest.raises(FullPromptValidationError, match="修改"):
        validate_full_prompt(text, [(0.0, 4.0)])


def test_prefix_numbers_are_not_timestamps():
    # 前缀含普通数字，不能被误解析为时间块。
    text = (
        "全局规则：第 12 秒和第 30 秒保持稳定，音量 50%。\n\n"
        "00:00.00–00:04.00\n保持：a\n修改：无\n删除：无\n禁止：无"
    )
    document = validate_full_prompt(text, [(0.0, 4.0)])
    assert document.global_prefix == "全局规则：第 12 秒和第 30 秒保持稳定，音量 50%。"
    assert len(document.blocks) == 1


def test_render_round_trip_preserves_blocks():
    document = validate_full_prompt(prompt_text(), [(0.0, 4.0), (4.0, 9.5)])
    rendered = render_full_prompt(document)
    reparsed = validate_full_prompt(rendered, [(0.0, 4.0), (4.0, 9.5)])
    assert [b.source_start_sec for b in reparsed.blocks] == [0.0, 4.0]
    assert reparsed.global_prefix == document.global_prefix


def test_expected_labels_uses_integer_centiseconds():
    labels = expected_full_prompt_labels([(0.0, 4.0), (4.0, 9.5)])
    assert labels == ["00:00.00–00:04.00", "00:04.00–00:09.50"]


def test_segment_derivation_clips_crossing_blocks_and_preserves_bodies():
    document = validate_full_prompt(
        three_shot_prompt(),
        [(0.0, 10.0), (10.0, 25.0), (25.0, 40.0)],
    )
    derived = derive_segment_prompt(
        document,
        segment_start_sec=18.0,
        segment_end_sec=32.0,
        batch_position=2,
        batch_size=3,
    )
    assert "片段 2/3" in derived
    assert "00:00.00–00:07.00" in derived
    assert "00:07.00–00:14.00" in derived
    assert "00:10.00–00:25.00" not in derived
    assert "镜头二保持正文" in derived
    assert "镜头三保持正文" in derived


def test_segment_derivation_full_coverage_8_seconds():
    document = validate_full_prompt(prompt_text(), [(0.0, 4.0), (4.0, 9.5)])
    derived = derive_segment_prompt(
        document,
        segment_start_sec=0.0,
        segment_end_sec=9.5,
        batch_position=1,
        batch_size=1,
    )
    assert "片段 1/1" in derived
    assert "00:00.00–00:04.00" in derived
    assert "00:04.00–00:09.50" in derived


def test_segment_derivation_exact_edge_coverage():
    document = validate_full_prompt(three_shot_prompt(), [(0.0, 10.0), (10.0, 25.0), (25.0, 40.0)])
    derived = derive_segment_prompt(
        document,
        segment_start_sec=10.0,
        segment_end_sec=25.0,
        batch_position=1,
        batch_size=2,
    )
    assert "00:00.00–00:15.00" in derived
    # 块头必须是相对时间；绝对来源区间只出现在服务端识别行，不作为块头出现。
    block_headers = [line for line in derived.split("\n") if "–" in line and ":" in line and line[:1].isdigit()]
    assert "00:10.00–00:25.00" not in block_headers


def test_segment_derivation_no_overlap_between_neighbors():
    document = validate_full_prompt(three_shot_prompt(), [(0.0, 10.0), (10.0, 25.0), (25.0, 40.0)])
    first = derive_segment_prompt(document, segment_start_sec=0.0, segment_end_sec=10.0, batch_position=1, batch_size=2)
    second = derive_segment_prompt(document, segment_start_sec=10.0, segment_end_sec=40.0, batch_position=2, batch_size=2)
    # 相邻段共享镜头 10–25：两段都含其裁剪后的相对块，但来源区间在各自片段内不重叠。
    assert "00:00.00–00:10.00" in first
    assert "00:00.00–00:15.00" in second


def test_segment_derivation_empty_intersection_raises():
    document = validate_full_prompt(three_shot_prompt(), [(0.0, 10.0), (10.0, 25.0), (25.0, 40.0)])
    with pytest.raises(FullPromptValidationError, match="无|覆盖"):
        derive_segment_prompt(document, segment_start_sec=50.0, segment_end_sec=60.0, batch_position=1, batch_size=1)


def test_segment_derivation_repeated_is_byte_identical():
    document = validate_full_prompt(three_shot_prompt(), [(0.0, 10.0), (10.0, 25.0), (25.0, 40.0)])
    first = derive_segment_prompt(document, segment_start_sec=18.0, segment_end_sec=32.0, batch_position=2, batch_size=3)
    second = derive_segment_prompt(document, segment_start_sec=18.0, segment_end_sec=32.0, batch_position=2, batch_size=3)
    assert first == second
