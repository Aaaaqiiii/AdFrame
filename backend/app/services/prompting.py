from collections.abc import Iterable


def compose_prompt(
    product_profile: str,
    visual_direction: str,
    audio_mode: str = "keep_original",
    audio_style: str = "",
    shot_instructions: Iterable[dict] = (),
) -> str:
    parts = [
        "严格保持参考视频的完整分镜、动作顺序和节奏。",
        f"原产品事实：{product_profile.strip()}。不得替换、删除或改变原产品身份、外形、包装、Logo 和文字。",
        "全局一致性：人物外观、产品形象、光线、空间关系和镜头连续性必须在全片保持一致。",
        "负面约束：不要新增未说明的人物、产品、品牌文字、镜头切换或动作。",
    ]
    if visual_direction.strip():
        parts.append(f"全局修改方向：{visual_direction.strip()}。")
    for shot in shot_instructions:
        facts = "；".join(f"{key}为 {value}" for key, value in shot["facts"].items() if value)
        changes = "；".join(f"{key}改为 {value}" for key, value in shot["changes"].items() if value)
        keep = "、".join(shot["keep"])
        lines = [f"{shot['start_sec']:.2f}–{shot['end_sec']:.2f} 秒："]
        if facts:
            lines.append(f"原视频事实：{facts}。")
        if changes:
            lines.append(f"用户修改：{changes}。")
        if keep:
            lines.append(f"保持不变：{keep}。")
        parts.append(" ".join(lines))
    if audio_mode == "add_style" and audio_style.strip():
        parts.append(f"音频风格：{audio_style.strip()}。")
    return "\n".join(parts)


def compose_replacement_prompt(
    product_profile: str,
    visual_direction: str,
    audio_mode: str = "keep_original",
    audio_style: str = "",
    shot_instructions: Iterable[dict] = (),
) -> str:
    parts = [
        "严格保持参考视频的广告结构、真实时间段、主要分镜、动作顺序、运镜和节奏。",
        f"将原视频中的产品统一替换为目标产品：{product_profile.strip()}。",
        "目标产品的外形、包装结构、开口方式、Logo、可见文字、材质和颜色在全片保持一致。所有使用动作必须匹配目标产品形态。",
        "未被用户明确修改的人物、背景、构图、光线和叙事关系尽量保持参考视频事实。",
        "负面约束：不得残留原产品，不得混合两种产品包装，不得改错 Logo 或包装文字，不得生成与产品形态冲突的手部动作。",
    ]
    if visual_direction.strip():
        parts.append(f"全局修改方向：{visual_direction.strip()}。")
    for shot in shot_instructions:
        facts = "；".join(f"{key}为 {value}" for key, value in shot["facts"].items() if value)
        changes = "；".join(f"{key}改为 {value}" for key, value in shot["changes"].items() if value)
        keep = "、".join(shot["keep"])
        lines = [f"{shot['start_sec']:.2f}–{shot['end_sec']:.2f} 秒："]
        if facts:
            lines.append(f"原视频事实：{facts}。")
        if shot.get("has_product"):
            lines.append(f"本镜头中的原产品必须替换为目标产品：{product_profile.strip()}。")
        if changes:
            lines.append(f"用户修改：{changes}。")
        if keep:
            lines.append(f"除产品替换外保持不变：{keep}。")
        parts.append(" ".join(lines))
    if audio_mode == "add_style" and audio_style.strip():
        parts.append(f"音频风格：{audio_style.strip()}。")
    return "\n".join(parts)
