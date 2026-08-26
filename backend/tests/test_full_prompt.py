import json

import pytest
from unittest.mock import patch

from app.core.config import Settings
from app.services.final_prompt import (
    build_full_prompt_prefix,
    generate_full_edit_prompt,
    generate_recreation_prompt,
    normalize_full_prompt_contract,
    recreation_audio_instruction,
    refine_recreation_prompt,
    transform_full_prompt,
    validate_recreation_prompt,
)

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


def test_full_edit_generation_normalizes_markdown_columns_and_wrapped_bodies() -> None:
    generated = (
        "00:00.00–00:04.00\n"
        "**保持：**\n- 人物与动作节奏\n- 固定构图\n"
        "- 修改: 替换产品。\n### 删除： 原字幕。\n禁止：不得新增文字。"
    )
    with patch("app.services.final_prompt._chat", return_value=generated) as chat:
        result = generate_full_edit_prompt(
            settings=Settings(comfly_api_key="test-key"),
            shot_slices=[{"start_sec": 0.0, "end_sec": 4.0, "action": "展示产品"}],
            deterministic_prefix="服务端锁定前缀", visual_direction="替换产品",
        )

    assert chat.call_count == 1
    assert "保持：- 人物与动作节奏 - 固定构图" in result
    assert "修改：替换产品。" in result
    validate_full_prompt(result, [(0.0, 4.0)])


def test_full_edit_generation_server_owns_labels_and_columns() -> None:
    generated = """```json
{"blocks":[{"keep":"保持动作与构图。","modify":"替换为已确认产品。","delete":"原字幕。","forbid":"不得新增文字。","label":"99:99.99–99:99.99"}]}
```"""
    with patch("app.services.final_prompt._chat", return_value=generated) as chat:
        result = generate_full_edit_prompt(
            settings=Settings(comfly_api_key="test-key"),
            shot_slices=[{"start_sec": 0.0, "end_sec": 4.0, "action": "展示产品"}],
            deterministic_prefix="服务端锁定前缀", visual_direction="替换产品",
        )

    assert chat.call_count == 1
    assert "99:99.99–99:99.99" not in result
    assert result.startswith(
        "服务端锁定前缀\n\n00:00.00–00:04.00\n"
        "保持：保持动作与构图。\n修改：替换为已确认产品。\n"
    )
    assert "删除：完整移除原片全部包装外字幕" in result
    assert "原字幕。" in result
    assert "禁止：禁止生成或复刻任何包装外文字" in result
    assert "不得新增文字。" in result
    system_message = chat.call_args.args[1][0]["content"]
    assert "只返回一个合法 JSON 对象" in system_message
    assert "不得返回时间标签" in system_message
    validate_full_prompt(result, [(0.0, 4.0)])


def test_full_edit_rejects_unknown_or_audio_only_selling_point_ids() -> None:
    def generated(selling_point_id: str) -> str:
        return json.dumps({"blocks": [{
            "keep": "保持构图。", "modify": "优化产品表现。", "delete": "无。", "forbid": "不得新增文字。",
            "selling_point_ids": [selling_point_id],
        }]}, ensure_ascii=False)

    with patch("app.services.final_prompt._chat", return_value=generated("SP2")):
        with pytest.raises(ValueError, match="不存在的产品卖点"):
            generate_full_edit_prompt(
                settings=Settings(comfly_api_key="test-key"),
                shot_slices=[{"start_sec": 0.0, "end_sec": 4.0}],
                deterministic_prefix="SP1（可视觉表现，用户提供）：乳霜质地",
                visual_direction="优化卖点",
            )

    with patch("app.services.final_prompt._chat", return_value=generated("SP1")):
        with pytest.raises(ValueError, match="当前未开启新音频"):
            generate_full_edit_prompt(
                settings=Settings(comfly_api_key="test-key"),
                shot_slices=[{"start_sec": 0.0, "end_sec": 4.0}],
                deterministic_prefix="SP1（仅音频，用户提供）：72小时长效柔顺",
                visual_direction="优化卖点",
            )


def test_full_edit_corrects_repeated_selling_point_with_validation_feedback() -> None:
    def generated(assignments: list[list[str]]) -> str:
        return json.dumps({"blocks": [{
            "keep": "保持构图。", "modify": "按分配结果表现产品。", "delete": "无。", "forbid": "不得新增文字。",
            "selling_point_ids": selling_point_ids,
        } for selling_point_ids in assignments]}, ensure_ascii=False)

    with patch("app.services.final_prompt._chat", side_effect=[
        generated([["SP5"], ["SP5"], ["SP5"]]),
        generated([["SP5"], ["SP5"], []]),
    ]) as chat:
        result = generate_full_edit_prompt(
            settings=Settings(comfly_api_key="test-key"),
            shot_slices=[
                {"start_sec": 0.0, "end_sec": 2.0},
                {"start_sec": 2.0, "end_sec": 4.0},
                {"start_sec": 4.0, "end_sec": 6.0},
            ],
            deterministic_prefix="SP5（可视觉表现，用户提供）：乳霜质地，好推开、吸收快",
            visual_direction="优化产品表现",
        )

    assert chat.call_count == 2
    correction = chat.call_args.args[1][-1]["content"]
    assert "产品卖点在过多镜头中重复：SP5" in correction
    assert "同一SP最多用于两个镜头" in correction
    validate_full_prompt(result, [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)])


def test_full_edit_person_replacement_removes_source_identity_before_chat() -> None:
    source_shots = [{
        "start_sec": 15.72,
        "end_sec": 16.84,
        "facts": {
            "people": "一名年轻男性，短黑发，身穿棕色短袖上衣，正面近距离出镜。",
            "action": "用白色毛巾擦拭湿发。",
        },
        "keep": ["保留年轻男性正面近距离出镜、湿发擦拭动作、白色毛巾、棕色短袖上衣。"],
    }]
    generated = json.dumps({"blocks": [{
        "keep": "保留目标人物正面近距离出镜、湿发擦拭动作和白色毛巾。",
        "modify": "人物身份与外观使用已确认参考图。",
        "delete": "删除原字幕。",
        "forbid": "不得改变动作、构图和节奏。",
    }]}, ensure_ascii=False)
    prefix = build_full_prompt_prefix(
        project_mode="preserve_product", product_profile="", product_image_purposes=[],
        people_reference="年轻成年女性，黑色中高丸子头。", people_reference_has_image=True,
        background_reference=None, audio_mode="none", audio_style="", replace_person=True,
    )

    with patch("app.services.final_prompt._chat", return_value=generated) as chat:
        result = generate_full_edit_prompt(
            settings=Settings(comfly_api_key="test-key"),
            shot_slices=source_shots,
            deterministic_prefix=prefix,
            visual_direction="保持原节奏",
            replace_person=True,
            project_mode="preserve_product",
        )

    request_text = chat.call_args.args[1][1]["content"]
    assert "男性" not in request_text
    assert "棕色短袖上衣" not in request_text
    assert "正面近距离出镜" in request_text
    assert "白色毛巾" in request_text
    assert "目标人物正面近距离出镜" in result
    assert "将原视频人物替换为人物参考图中的人物，并保持脸部身份和整体形象跨镜头一致" in result
    assert "保留原镜头的人数、位置、姿态、动作范围、力度和镜头节奏" in result
    assert "已确认的同一目标人物" not in result
    assert source_shots[0]["facts"]["people"].startswith("一名年轻男性")


def test_full_edit_person_replacement_rewrites_conflicting_person_ban() -> None:
    source_shots = [{
        "start_sec": 0.0, "end_sec": 4.0,
        "facts": {"people": "一名人物正面出镜。"}, "keep": [],
    }]
    generated = json.dumps({"blocks": [{
        "keep": "保留原人物正面出镜。", "modify": "无。", "delete": "无。",
        "forbid": "不得改变人物、场景、构图和节奏。",
    }]}, ensure_ascii=False)
    prefix = build_full_prompt_prefix(
        project_mode="preserve_product", product_profile="", product_image_purposes=[],
        people_reference="年轻成年女性。", people_reference_has_image=True,
        background_reference=None, audio_mode="none", audio_style="", replace_person=True,
    )

    with patch("app.services.final_prompt._chat", return_value=generated):
        result = generate_full_edit_prompt(
            settings=Settings(comfly_api_key="test-key"), shot_slices=source_shots,
            deterministic_prefix=prefix, visual_direction="保持节奏", replace_person=True,
            project_mode="preserve_product",
        )

    assert "保留原人物" not in result
    assert "不得改变人物" not in result
    assert "不得偏离人物参考图确定的脸部身份和整体形象" in result


def test_person_replacement_prefix_limits_reference_video_identity_priority() -> None:
    prefix = build_full_prompt_prefix(
        project_mode="preserve_product", product_profile="", product_image_purposes=[],
        people_reference="年轻成年女性，黑色丸子头。", background_reference=None,
        audio_mode="none", audio_style="", replace_person=True, people_reference_has_image=True,
    )
    assert "不得继承原视频人物的脸部、性别、身份或外观" in prefix
    assert "目标人物参考图是人物脸部、身份和整体外观的最高优先级" in prefix
    assert "原参考视频是时间轴、动作、构图、运镜、节奏和镜头顺序的最高优先级参考" not in prefix


def test_text_only_person_reference_never_claims_an_image_exists() -> None:
    prefix = build_full_prompt_prefix(
        project_mode="preserve_product", product_profile="", product_image_purposes=[],
        people_reference="25岁左右女性，黑色齐肩直发，白色衬衫。",
        people_reference_has_image=False,
        background_reference=None, audio_mode="none", audio_style="", replace_person=True,
    )

    assert "已确认人物文字档案是人物身份和整体外观的最高优先级" in prefix
    assert "人物参考图" not in prefix
    assert "人物脸部" not in prefix


def test_text_only_person_reference_removes_image_claims_from_model_blocks() -> None:
    prefix = build_full_prompt_prefix(
        project_mode="preserve_product", product_profile="", product_image_purposes=[],
        people_reference="25岁左右女性，黑色齐肩直发，白色衬衫。",
        people_reference_has_image=False,
        background_reference=None, audio_mode="none", audio_style="", replace_person=True,
    )
    generated = json.dumps({"blocks": [{
        "keep": "保持人物站位。",
        "modify": "人物脸部完全按照目标人物参考图。",
        "delete": "无。",
        "forbid": "不得偏离人物参考图。",
    }]}, ensure_ascii=False)

    with patch("app.services.final_prompt._chat", return_value=generated):
        result = generate_full_edit_prompt(
            settings=Settings(comfly_api_key="test-key"),
            shot_slices=[{
                "start_sec": 0.0, "end_sec": 3.0,
                "facts": {"people": "一名男性站立。"}, "keep": [],
            }],
            deterministic_prefix=prefix, visual_direction="替换人物", replace_person=True,
            project_mode="preserve_product",
        )

    assert "人物参考图" not in result
    assert "已确认人物文字档案" in result
    assert "将原视频人物替换为人物文字档案中定义的目标人物" in result
    assert "保留原镜头的人数、位置、姿态、动作范围、力度和镜头节奏" in result
    assert "已确认的同一目标人物" not in result


def test_person_contract_rules_are_idempotent_during_prompt_normalization() -> None:
    prefix = build_full_prompt_prefix(
        project_mode="preserve_product", product_profile="", product_image_purposes=[],
        people_reference="25岁左右女性，黑色齐肩直发。", people_reference_has_image=False,
        background_reference=None, audio_mode="none", audio_style="", replace_person=True,
    )
    source_shots = [{
        "start_sec": 0.0, "end_sec": 3.0,
        "facts": {"people": "一名男性站立。"}, "keep": [],
    }]
    prompt = prefix + "\n\n" + (
        "00:00.00–00:03.00\n保持：保持人物站位。\n修改：无。\n"
        "删除：无。\n禁止：无。"
    )

    normalized = normalize_full_prompt_contract(
        prompt, [(0.0, 3.0)], source_shots,
        project_mode="preserve_product", replace_person=True,
    )
    normalized_again = normalize_full_prompt_contract(
        normalized, [(0.0, 3.0)], source_shots,
        project_mode="preserve_product", replace_person=True,
    )

    assert normalized_again == normalized
    assert normalized_again.count("保留原镜头的人数、位置、姿态、动作范围、力度和镜头节奏") == 1
    assert normalized_again.count("将原视频人物替换为人物文字档案中定义的目标人物") == 1


def test_user_direction_is_first_and_overrides_reference_content() -> None:
    direction = "人物使用参考图，删除全部字幕，背景改为浴室。"
    prefix = build_full_prompt_prefix(
        project_mode="replace_product", product_profile="罐装发膜", product_image_purposes=["正面"],
        people_reference="同一成年女性", background_reference="明亮浴室",
        audio_mode="keep_original", audio_style="", replace_person=True,
        user_direction=direction, people_reference_has_image=True,
    )

    assert prefix.startswith(f"【最高优先级：用户改编要求】\n{direction}")
    assert "不得弱化、遗漏、反向解释" in prefix
    assert "视频1仅参考原视频格式" in prefix
    assert "不继承视频1中的字幕" in prefix
    assert "所有出现人物的镜头" in prefix


def test_full_edit_corrects_source_identity_leak_with_validation_feedback() -> None:
    source_shots = [{
        "start_sec": 15.72,
        "end_sec": 16.84,
        "facts": {"people": "一名年轻男性，短黑发，正面近距离出镜。"},
        "keep": ["保留年轻男性正面近距离出镜。"],
    }]
    leaked = json.dumps({"blocks": [{
        "keep": "保留年轻男性正面近距离出镜。",
        "modify": "无。",
        "delete": "无。",
        "forbid": "不得改变构图。",
    }]}, ensure_ascii=False)
    corrected = json.dumps({"blocks": [{
        "keep": "保留目标人物正面近距离出镜。",
        "modify": "使用目标人物参考图中的同一人物。",
        "delete": "无。",
        "forbid": "不得改变构图。",
    }]}, ensure_ascii=False)

    with patch("app.services.final_prompt._chat", side_effect=[leaked, corrected]) as chat:
        result = generate_full_edit_prompt(
            settings=Settings(comfly_api_key="test-key"),
            shot_slices=source_shots,
            deterministic_prefix="人物外观以已确认参考图为准：年轻成年女性，黑色中高丸子头。",
            visual_direction="保持原节奏",
            replace_person=True,
        )

    assert chat.call_count == 2
    assert "人物替换结果仍包含原人物身份：男性" in chat.call_args.args[1][-1]["content"]
    assert "男性" not in result
    assert "目标人物正面近距离出镜" in result


def test_full_edit_repair_normalizes_fenced_markdown_before_validation() -> None:
    incomplete = "00:00.00–00:04.00\n修改：替换产品。\n删除：无。\n禁止：无。"
    repaired = (
        "```text\n00:00.00–00:04.00 **保持：** 保持动作。\n"
        "- 修改: 替换产品。\n- 删除：无。\n- 禁止：不得新增文字。\n```"
    )
    with patch("app.services.final_prompt._chat", side_effect=[incomplete, repaired]) as chat:
        result = generate_full_edit_prompt(
            settings=Settings(comfly_api_key="test-key"),
            shot_slices=[{"start_sec": 0.0, "end_sec": 4.0, "action": "展示产品"}],
            deterministic_prefix="服务端锁定前缀", visual_direction="替换产品",
        )

    assert chat.call_count == 2
    assert "保持：保持动作。" in result
    validate_full_prompt(result, [(0.0, 4.0)])


def test_full_edit_generation_normalizes_described_bold_and_inline_columns() -> None:
    generated = (
        "00:00.00–00:04.00\n1. **保持**（锁定内容）：人物动作。 "
        "2. 修改事项: 替换产品。 3、删除部分：原字幕。 4. 禁止要求：不得新增文字。"
    )
    with patch("app.services.final_prompt._chat", return_value=generated) as chat:
        result = generate_full_edit_prompt(
            settings=Settings(comfly_api_key="test-key"),
            shot_slices=[{"start_sec": 0.0, "end_sec": 4.0, "action": "展示产品"}],
            deterministic_prefix="服务端锁定前缀", visual_direction="替换产品",
        )

    assert chat.call_count == 1
    assert "保持：人物动作。" in result
    assert "删除：完整移除原片全部包装外字幕" in result
    assert "原字幕。" in result
    validate_full_prompt(result, [(0.0, 4.0)])


def test_standalone_recreation_prompt_is_self_contained_and_complete() -> None:
    block = "画面：完整场景。\n动作：连续动作。\n镜头：固定中景。\n光线：柔光。\n声音：环境音。\n禁止：不得新增文字。"
    text = f"全局一致性：人物和产品跨镜头一致。\n\n00:00.00–00:04.00\n{block}\n\n00:04.00–00:09.50\n{block}"
    assert validate_recreation_prompt(text, shot_ranges(), "replace_product") == text
    with pytest.raises(ValueError, match="不能依赖原视频"):
        validate_recreation_prompt(text + "\n保持原视频动作。", shot_ranges(), "replace_product")
    with pytest.raises(ValueError, match="不能依赖原视频"):
        validate_recreation_prompt(text + "\n参考视频中的构图不变。", shot_ranges(), "replace_product")
    with pytest.raises(ValueError, match="时间重复"):
        validate_recreation_prompt(text + "\n00:00.00–00:04.00", shot_ranges(), "replace_product")
    with pytest.raises(ValueError, match="栏目缺失或重复"):
        validate_recreation_prompt(text.replace("动作：连续动作。", "动作：连续动作。\n动作：补充动作。", 1), shot_ranges(), "replace_product")
    with pytest.raises(ValueError, match="不能替换产品"):
        validate_recreation_prompt(text.replace("完整场景", "将原产品替换为另一款产品", 1), shot_ranges(), "preserve_product")
    reversed_text = f"全局一致性：一致。\n\n00:04.00–00:09.50\n{block}\n\n00:00.00–00:04.00\n{block}"
    with pytest.raises(ValueError, match="时间顺序错误"):
        validate_recreation_prompt(reversed_text, shot_ranges(), "replace_product")


def test_standalone_recreation_generation_includes_all_product_image_purposes() -> None:
    block = "画面：目标产品置于桌面。\n动作：手拿起产品。\n镜头：固定中景。\n光线：柔光。\n声音：无。\n禁止：不得改变包装。"
    result = f"全局一致性：目标产品一致。\n\n00:00.00–00:04.00\n{block}"
    with patch("app.services.final_prompt._chat", return_value=result) as chat:
        generate_recreation_prompt(
            settings=Settings(comfly_api_key="test-key"),
            shot_slices=[{"start_sec": 0.0, "end_sec": 4.0, "product": "瓶装产品"}],
            visual_direction="完整复刻", product_profile="绿色瓶身", person_profile="",
            replace_product=True, replace_person=False, audio_mode="none", audio_style="",
            product_purpose_lines=["正面：锁定正面结构", "侧面：锁定侧面结构", "开口：锁定开口结构"],
            compatibility_conflicts=[{"shot_id": "s1", "severity": "adaptable", "reason": "原动作不适用", "suggestion": "旋开并取用"}],
        )
    request_text = chat.call_args.args[1][1]["content"]
    assert all(label in request_text for label in ("正面", "侧面", "开口"))
    assert "旋开并取用" in request_text
    assert "不生成音频" in request_text


def test_standalone_text_only_person_reference_does_not_claim_an_image() -> None:
    block = (
        "画面：目标人物参考图中的同一年轻女性站在室内。\n动作：人物正面展示。\n"
        "镜头：固定中景。\n光线：柔光。\n声音：无音频。\n禁止：不得新增文字。"
    )
    generated = f"全局一致性：人物跨镜头一致。\n\n00:00.00–00:04.00\n{block}"
    with patch("app.services.final_prompt._chat", return_value=generated):
        result = generate_recreation_prompt(
            settings=Settings(comfly_api_key="test-key"),
            shot_slices=[{
                "start_sec": 0.0, "end_sec": 4.0,
                "facts": {"people": "一名男性正面展示。"}, "keep": [],
            }],
            visual_direction="完整复刻", product_profile="",
            person_profile="25岁左右女性，黑色齐肩直发，白色衬衫。",
            person_reference_has_image=False,
            replace_product=False, replace_person=True, audio_mode="none", audio_style="",
        )

    assert "已确认人物文字档案" in result
    assert "人物参考图" not in result


def test_standalone_recreation_generation_repairs_only_missing_shot() -> None:
    block = "画面：完整场景。\n动作：连续动作。\n镜头：固定中景。\n光线：柔光。\n声音：无音频。\n禁止：不得新增文字。"
    first_only = f"全局一致性：人物一致。\n\n00:00.00–00:04.00\n{block}"
    repaired_second = f"00:04.00–00:09.50\n{block.replace('固定中景', '近景缓慢推近')}"
    with patch("app.services.final_prompt._chat", side_effect=[first_only, repaired_second]) as chat:
        result = generate_recreation_prompt(
            settings=Settings(comfly_api_key="test-key"),
            shot_slices=[
                {"shot_id": "s1", "start_sec": 0.0, "end_sec": 4.0, "facts": {"action": "展示"}},
                {"shot_id": "s2", "start_sec": 4.0, "end_sec": 9.5, "facts": {"action": "旋转"}},
            ],
            visual_direction="完整复刻", product_profile="", person_profile="",
            replace_product=False, replace_person=False, audio_mode="none", audio_style="",
        )
    assert chat.call_count == 2
    assert result.index("00:00.00–00:04.00") < result.index("00:04.00–00:09.50")
    assert "近景缓慢推近" in result


def test_recreation_audio_modes_have_distinct_contracts() -> None:
    assert "不生成音频" in recreation_audio_instruction("none", "")
    assert "不得新增对白" in recreation_audio_instruction("auto", "")
    assert "开盖声" in recreation_audio_instruction("custom", "保留开盖声")
    with pytest.raises(ValueError, match="填写音频要求"):
        recreation_audio_instruction("custom", "")
    with pytest.raises(ValueError, match="未知"):
        recreation_audio_instruction("keep_original", "")


def test_standalone_recreation_refinement_preserves_self_contained_schema() -> None:
    block = "画面：完整场景。\n动作：连续动作。\n镜头：固定中景。\n光线：柔光。\n声音：环境音。\n禁止：不得新增文字。"
    source = f"全局一致性：人物一致。\n\n00:00.00–00:04.00\n{block}"
    revised = source.replace("固定中景", "中景缓慢推近")
    with patch("app.services.final_prompt._chat", return_value=revised):
        assert "中景缓慢推近" in refine_recreation_prompt(
            settings=Settings(comfly_api_key="test-key"), source_text=source,
            instruction="镜头缓慢推近", shot_ranges=[(0.0, 4.0)], project_mode="replace_product",
        )
    with patch("app.services.final_prompt._chat", return_value=revised + "\n沿用参考视频节奏"):
        with pytest.raises(ValueError, match="不能依赖原视频"):
            refine_recreation_prompt(
                settings=Settings(comfly_api_key="test-key"), source_text=source,
                instruction="镜头缓慢推近", shot_ranges=[(0.0, 4.0)], project_mode="replace_product",
            )


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


def test_format_time_label_stable_under_float_noise():
    # 浮点噪声不得改变标签：4.1299999 与 4.12 必须同标签。
    assert format_time_label(4.12, 9.5) == format_time_label(4.1299999, 9.5000001)
    assert format_time_label(4.12, 9.5) == "00:04.12–00:09.50"
    assert format_time_label(0.1 + 0.2, 1.0) == "00:00.30–00:01.00"


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


def test_segment_derivation_clips_crossing_blocks_and_uses_provider_action_format():
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
    assert "镜头二保持正文" not in derived
    assert "镜头三保持正文" not in derived
    assert derived.count("镜头形式：") == 2
    assert derived.count("改后动作：") == 2
    assert derived.count("画面清理：") == 2


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


def test_segment_derivation_compares_coverage_at_prompt_centisecond_precision():
    document = validate_full_prompt(
        "00:00.00–00:15.25\n保持：a\n修改：无\n删除：无\n禁止：无",
        [(0.0, 15.255011)],
    )

    derived = derive_segment_prompt(
        document,
        segment_start_sec=0.0,
        segment_end_sec=15.255011,
        batch_position=1,
        batch_size=1,
    )

    assert "片段 1/1：对应视频1 00:00.00–00:15.25" in derived


def test_segment_derivation_rejects_the_next_centisecond_beyond_coverage():
    document = validate_full_prompt(
        "00:00.00–00:15.25\n保持：a\n修改：无\n删除：无\n禁止：无",
        [(0.0, 15.25)],
    )

    with pytest.raises(FullPromptValidationError, match="超出"):
        derive_segment_prompt(
            document,
            segment_start_sec=0.0,
            segment_end_sec=15.26,
            batch_position=1,
            batch_size=1,
        )


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


def test_transform_source_body_not_misled_by_prefix_colon():
    """源正文定位用第一个时间标签，前缀中的 00: 不会被误切成正文起点。"""
    from unittest.mock import patch
    from app.services.final_prompt import build_full_prompt_prefix, transform_full_prompt
    from app.core.config import Settings
    prefix = (
        "全局规则：视频第 00:12 秒开始，音量 00:00。\n"
        + build_full_prompt_prefix(project_mode="preserve_product", product_profile="", product_image_purposes=[], people_reference=None, background_reference=None, audio_mode="keep_original", audio_style="")
    )
    source = prefix + "\n\n" + "00:00.00–00:04.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。"
    with patch("app.services.final_prompt._chat", return_value="00:00.00–00:04.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。"):
        result = transform_full_prompt(
            settings=Settings(comfly_api_key="x"),
            source_text=source,
            instruction="增强光线",
            expected_shot_ranges=[(0.0, 4.0)],
            required_prefixes=("【参考范围】", "【画面文字】"),
            project_mode="preserve_product",
        )
    assert result.startswith(prefix)


def test_transform_uses_structured_content_and_restores_server_format():
    prefix = "原参考视频是时间轴最高优先级参考。\n禁止新增字幕。"
    source = prefix + "\n\n" + (
        "00:00.00–00:04.00\n保持：原动作。\n修改：无。\n删除：无。\n禁止：不得新增文字。\n\n"
        "00:04.00–00:09.50\n保持：原构图。\n修改：无。\n删除：无。\n禁止：不得新增文字。"
    )
    generated = json.dumps({
        "blocks": [
            {"keep": "原动作。", "modify": "增强柔光。", "delete": "无。", "forbid": "不得新增文字。"},
            {"keep": "原构图。", "modify": "增强柔光。", "delete": "无。", "forbid": "不得新增文字。"},
        ]
    }, ensure_ascii=False)
    with patch("app.services.final_prompt._chat", return_value=generated):
        result = transform_full_prompt(
            settings=Settings(comfly_api_key="x"),
            source_text=source,
            instruction="增强光线",
            expected_shot_ranges=shot_ranges(),
            required_prefixes=("原参考视频是时间轴", "禁止新增字幕"),
            project_mode="preserve_product",
        )

    assert result.startswith(prefix)
    assert result.count("保持：") == 2
    assert result.count("修改：增强柔光。") == 2
    validate_full_prompt(result, shot_ranges())


def test_transform_upgrades_legacy_person_rule_to_explicit_text_source() -> None:
    prefix = build_full_prompt_prefix(
        project_mode="preserve_product", product_profile="", product_image_purposes=[],
        people_reference="25岁左右女性，黑色齐肩直发，白色衬衫。",
        people_reference_has_image=False,
        background_reference=None, audio_mode="none", audio_style="", replace_person=True,
    )
    source = prefix + "\n\n" + (
        "00:00.00–00:04.00\n"
        "保持：原镜头动作。\n"
        "修改：将原视频人物替换为已确认的同一目标人物，并保持该目标人物跨镜头一致。\n"
        "删除：原字幕。\n"
        "禁止：不得偏离已确认的目标人物身份和整体外观。"
    )
    generated = json.dumps({"blocks": [{
        "keep": "原镜头动作。", "modify": "增强柔光。", "delete": "原字幕。",
        "forbid": "不得改变构图。",
    }]}, ensure_ascii=False)

    with patch("app.services.final_prompt._chat", return_value=generated):
        result = transform_full_prompt(
            settings=Settings(comfly_api_key="x"), source_text=source, instruction="增强柔光",
            expected_shot_ranges=[(0.0, 4.0)], required_prefixes=("【参考范围】", "【画面文字】"),
            project_mode="preserve_product", replace_person=True,
        )

    assert "将原视频人物替换为人物文字档案中定义的目标人物" in result
    assert "已确认的同一目标人物" not in result
