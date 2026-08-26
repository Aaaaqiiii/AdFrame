from __future__ import annotations

import copy
import json
import re
import time
from typing import Sequence
from uuid import UUID

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.volcengine_vision import VisionConfigurationError
from app.db.models import Asset, GenerationSegment, Job, Project, PromptRevision, Shot, ShotEdit
from app.services.full_prompt import FullPromptValidationError, expected_full_prompt_labels, parse_full_prompt, validate_full_prompt
from app.services.product_compatibility import check_product_compatibility
from app.services.product_rules import confirmed_target_product_assets, contains_product_replacement
from app.services.reference_profiles import (
    load_structure,
    product_prompt_profile,
    product_selling_point_cards,
    render_selling_point_cards,
)


def _time_label(start_sec: float, end_sec: float) -> str:
    def stamp(value: float) -> str:
        return f"{int(value // 60):02d}:{value % 60:05.2f}"
    return f"{stamp(start_sec)}–{stamp(end_sec)}"


def _chat(settings: Settings, messages: list[dict], max_tokens: int = 20000) -> str:
    """调用当前配置的 GPT-5.6 兼容接口，并统一校验空响应。"""
    for attempt in range(3):
        try:
            response = requests.post(
                f"{settings.comfly_vision_base_url.rstrip('/')}/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.comfly_api_key}", "Content-Type": "application/json"},
                json={"model": settings.comfly_vision_model, "messages": messages, "temperature": 0.1, "max_tokens": max_tokens},
                timeout=(15, 300),
            )
            break
        except (
            requests.exceptions.ProxyError,
            requests.exceptions.ConnectionError,
            requests.exceptions.ConnectTimeout,
        ):
            if attempt == 2:
                raise
            # 只重试当前镜头请求，不让整个长提示词任务从头再跑。
            time.sleep(attempt + 1)
    response.raise_for_status()
    text = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if not text:
        raise ValueError("GPT 未返回提示词")
    return text


def _actionable_bodies_text(text: str) -> str:
    """提取完整提示词全部时间块的 修改/删除 正文，供替换检测（不含前缀与禁止栏目）。"""
    try:
        document = validate_full_prompt(text, [(b.source_start_sec, b.source_end_sec) for b in parse_full_prompt(text).blocks])
    except (FullPromptValidationError, ValueError):
        return text
    parts = []
    for block in document.blocks:
        parts.append(block.modify)
        parts.append(block.delete)
    return "\n".join(parts)


def build_full_prompt_prefix(
    *,
    project_mode: str,
    product_profile: str,
    product_image_purposes: list[str],
    people_reference: str | None,
    background_reference: str | None,
    audio_mode: str,
    audio_style: str,
    selling_point_cards: list[dict[str, str]] | None = None,
    replace_person: bool = False,
    user_direction: str = "",
    people_reference_has_image: bool = False,
) -> str:
    """服务端确定性前缀：模式锁定、产品档案与用途、人物/背景、音频、禁止规则。"""
    lines: list[str] = []
    if user_direction.strip():
        lines.append(
            "【最高优先级：用户改编要求】\n"
            f"{user_direction.strip()}\n"
            "后续镜头描述、参考视频内容或 AI 补充内容与本段冲突时，一律以本段为准；"
            "不得弱化、遗漏、反向解释或恢复用户明确要求删除的内容。"
        )
    excluded = ["字幕、标题、卖点文字、促销文字、贴纸、角标、水印、平台标识和其他包装外文字"]
    if project_mode == "replace_product":
        excluded.append("原产品身份、原品牌和原包装")
    if replace_person:
        excluded.append("原人物的脸部、身份、性别和整体外观")
    lines.append(
        "【参考范围】\n"
        "视频1仅参考原视频格式、画幅比例、片段时长、镜头边界、镜头顺序、机位、构图、运镜节奏、"
        "动作轨迹、叙事功能和表情强度；未明确修改的画面内容只作弱参考，不得覆盖用户改编要求。\n"
        "不继承视频1中的" + "、".join(excluded) + "。"
    )
    lines.append("【全局执行】")
    if project_mode == "preserve_product":
        lines.append("保持原产品不变，禁止替换、删除或重新设计原产品。")
    else:
        lines.append(f"目标产品必须匹配已确认产品档案：\n{product_profile}")
        if product_image_purposes:
            lines.append("产品参考图用途（按名称锁定对应结构，不得省略）：\n" + "\n".join(f"- {line}" for line in product_image_purposes))
        if selling_point_cards:
            lines.append(
                "可用产品卖点（均为用户提供信息；只选与现有镜头自然匹配的内容，不要求全部使用）：\n"
                + render_selling_point_cards(selling_point_cards)
            )
    if people_reference:
        if people_reference_has_image:
            lines.append(
                "【人物身份硬约束】\n"
                "所有出现人物的镜头，人物脸部、身份、发型和整体形象均以目标人物参考图为准。"
                "原参考视频人物只参考人数、位置、姿态、动作轨迹和表情强度；不得继承原视频人物的脸部、性别、身份或外观。\n"
                f"目标人物参考图是人物脸部、身份和整体外观的最高优先级；下列文字档案只作关键锚点辅助：\n{people_reference}\n"
                "逐镜只写“使用目标人物参考图中的同一人物”，不重复展开五官、妆容、身材等细节。"
            )
        else:
            lines.append(
                "【人物身份硬约束】\n"
                "所有出现人物的镜头，人物年龄段、性别表达、发型、主服装和显著特征均以已确认人物文字档案为准。"
                "原参考视频人物只参考人数、位置、姿态、动作轨迹和表情强度；不得继承原视频人物的脸部、性别、身份或外观。\n"
                f"已确认人物文字档案是人物身份和整体外观的最高优先级：\n{people_reference}\n"
                "逐镜只写“使用已确认人物文字档案中的同一人物”，保持新人物跨镜头一致，不虚构档案之外的身份和外观细节。"
            )
    if background_reference:
        lines.append(f"背景必须匹配已确认背景参考：{background_reference}")
    if audio_mode in {"custom", "add_style"}:
        lines.append(f"允许生成新音频；严格按照音频要求执行：{audio_style}；不新增未要求的对白、旁白或营销口播。")
    elif audio_mode == "auto":
        lines.append("允许自动生成与画面动作匹配的环境音、动作音和背景音乐；不新增对白、旁白或营销口播。")
    else:
        lines.append("不生成新音频；分段参考片段不携带原音频，不宣称保留原 BGM。")
    allowed_package_text = (
        "只允许原视频产品实体包装上已有的固有品牌、品名和标签文字"
        if project_mode == "preserve_product"
        else "只允许目标产品参考图确认的实体包装固有品牌、品名和标签文字"
    )
    lines.append(
        "【画面文字】\n"
        "不生成、不复刻任何字幕、标题、卖点文字、促销文字、装饰字符、贴纸、角标、水印或平台标识；"
        "完整移除原片已有包装外文字并自然修复遮挡区域，不保留文字轮廓或残影。"
        f"{allowed_package_text}，不在包装外重新排版或抄写。"
    )
    lines.append(
        "【禁止】\n"
        "禁止任何包装外文字、乱码和不存在的包装文字；不得新增未确认动作、人物或产品功效。"
    )
    return "\n".join(lines)


_PERSON_GENDER_RE = re.compile(
    r"(?:一名|一位|一个)?(?:年轻|成年|中年|老年)?"
    r"(?:男性|女性|男士|女士|男生|女生|男孩|女孩|男人|女人|男子|女子|男模特|女模特|男演员|女演员)"
)
_PERSON_AGE_RE = re.compile(r"(?:约|大约)?\d{1,3}\s*岁(?:左右|上下)?(?:的)?")
_PERSON_APPEARANCE_RE = re.compile(
    r"男性|女性|男士|女士|男生|女生|男孩|女孩|男人|女人|男子|女子|男模特|女模特|男演员|女演员|"
    r"儿童|少年|少女|年轻|成年|中年|老年|老人|"
    r"短发|长发|卷发|直发|黑发|金发|棕发|白发|丸子头|马尾|刘海|光头|发型|"
    r"身穿|穿着|身着|上衣|衬衫|T恤|背心|外套|裙|裤|服装|衣着|"
    r"五官|脸型|面容|妆容|肤色|身材|身形|体型|高挑|矮小"
)
_PERSON_IDENTITY_TERM_RE = re.compile(
    r"男性|女性|男士|女士|男生|女生|男孩|女孩|男人|女人|男子|女子|男模特|女模特|男演员|女演员|"
    r"儿童|少年|少女|年轻|成年|中年|老年|老人|"
    r"短发|长发|卷发|直发|黑发|金发|棕发|白发|丸子头|马尾|刘海|光头"
)


def _person_motion_context(text: str) -> str:
    """只从原人物描述中保留与身份外观无关的镜头信息。"""
    parts = [part.strip() for part in re.split(r"[，,、；;。]+", text or "") if part.strip()]
    return "、".join(part for part in parts if not _PERSON_APPEARANCE_RE.search(part))


def _sanitize_person_keep(text: str) -> str:
    """把 keep 中的原人物身份改成目标人物，并删除纯外观/服装分句。"""
    cleaned: list[str] = []
    for part in (item.strip() for item in re.split(r"[，,、；;。]+", text or "")):
        if not part:
            continue
        part = _PERSON_GENDER_RE.sub("目标人物", part)
        part = _PERSON_AGE_RE.sub("", part)
        if _PERSON_APPEARANCE_RE.search(part):
            continue
        cleaned.append(part)
    return "、".join(cleaned)


def prepare_person_replacement_shots(
    shot_slices: list[dict], *, reference_has_image: bool = False,
) -> list[dict]:
    """生成前移除原人物身份；保留镜头动作、位置、姿态和表情语义。"""
    prepared = copy.deepcopy(shot_slices)
    target_source = "人物参考图中的人物" if reference_has_image else "人物文字档案中定义的目标人物"
    for shot in prepared:
        facts = shot.get("facts")
        if not isinstance(facts, dict):
            continue
        source_people = str(facts.get("people") or "").strip()
        if source_people:
            motion_context = _person_motion_context(source_people)
            facts["people"] = (
                f"{target_source}；保留本镜头原有人数、出镜位置、姿态和表情强度，"
                "不继承原人物的身份、性别、年龄、外貌、发型、体型或服装"
                + (f"；镜头信息：{motion_context}" if motion_context else "")
            )
        keep = shot.get("keep")
        if isinstance(keep, list):
            shot["keep"] = [value for item in keep if (value := _sanitize_person_keep(str(item)))]
    return prepared


def validate_person_replacement_output(text: str, source_shots: list[dict], target_person_reference: str) -> str:
    """禁止最终提示词重新锁定仅属于原人物的明确身份词。"""
    source_text = "\n".join(
        str(shot.get("facts", {}).get("people") or "") + "\n" + "\n".join(map(str, shot.get("keep") or []))
        for shot in source_shots
        if isinstance(shot.get("facts"), dict)
    )
    source_terms = set(_PERSON_IDENTITY_TERM_RE.findall(source_text))
    leaked = sorted(term for term in source_terms if term not in target_person_reference and term in text)
    if leaked:
        raise ValueError(f"人物替换结果仍包含原人物身份：{'、'.join(leaked)}")
    return text


_IMAGE_TARGET_PERSON_MODIFY = "将原视频人物替换为人物参考图中的人物，并保持脸部身份和整体形象跨镜头一致"
_IMAGE_TARGET_PERSON_LOCK = "不得偏离人物参考图确定的脸部身份和整体形象"
_TEXT_TARGET_PERSON_MODIFY = (
    "将原视频人物替换为人物文字档案中定义的目标人物，并确保其年龄段、性别表达、发型、"
    "主服装和显著特征在所有镜头中保持一致"
)
_TEXT_TARGET_PERSON_LOCK = "不得偏离人物文字档案定义的年龄段、性别表达、发型、主服装和显著特征"
_LEGACY_TARGET_PERSON_MODIFY = "将原视频人物替换为已确认的同一目标人物，并保持该目标人物跨镜头一致"
_LEGACY_TARGET_PERSON_LOCK = "不得偏离已确认的目标人物身份和整体外观"
_PERSON_MOTION_KEEP = "保留原镜头的人数、位置、姿态、动作范围、力度和镜头节奏"
_PERSON_MODIFY_RULES = (_IMAGE_TARGET_PERSON_MODIFY, _TEXT_TARGET_PERSON_MODIFY, _LEGACY_TARGET_PERSON_MODIFY)
_PERSON_LOCK_RULES = (_IMAGE_TARGET_PERSON_LOCK, _TEXT_TARGET_PERSON_LOCK, _LEGACY_TARGET_PERSON_LOCK)
_PERSON_REPLACEMENT_PREFIX_MARKER = "目标人物参考图是人物脸部、身份和整体外观的最高优先级"
_TEXT_PERSON_REPLACEMENT_PREFIX_MARKER = "已确认人物文字档案是人物身份和整体外观的最高优先级"
_REMOVE_EXTERNAL_TEXT = "完整移除原片全部包装外字幕、标题、卖点文字、促销文字、贴纸、角标、水印和平台标识，并自然修复遮挡区域"


def _person_replacement_rules(reference_has_image: bool) -> tuple[str, str]:
    return (
        (_IMAGE_TARGET_PERSON_MODIFY, _IMAGE_TARGET_PERSON_LOCK)
        if reference_has_image
        else (_TEXT_TARGET_PERSON_MODIFY, _TEXT_TARGET_PERSON_LOCK)
    )


def _forbid_external_text(project_mode: str) -> str:
    allowed = (
        "原视频产品实体包装上已有的固有标签文字"
        if project_mode == "preserve_product"
        else "目标产品参考图确认的实体包装固有标签文字"
    )
    return f"禁止生成或复刻任何包装外文字，只允许{allowed}"


def person_replacement_contract_ready(text: str) -> bool:
    """旧提示词没有正确拆分视频与人物参考优先级，禁止再次用于付费生成。"""
    value = text or ""
    return _PERSON_REPLACEMENT_PREFIX_MARKER in value or _TEXT_PERSON_REPLACEMENT_PREFIX_MARKER in value


def generation_prompt_contract_ready(text: str, user_direction: str = "") -> bool:
    """Reject saved prompts that predate the provider-reference and text-cleanup contract."""
    value = text or ""
    if "【参考范围】" not in value or "【画面文字】" not in value:
        return False
    direction = (user_direction or "").strip()
    if direction and ("【最高优先级：用户改编要求】" not in value or direction not in value):
        return False
    return True


def _shot_has_person(shot: dict) -> bool:
    facts = shot.get("facts")
    people = facts.get("people") if isinstance(facts, dict) else shot.get("people")
    value = re.sub(r"\s+", "", str(people or ""))
    return bool(value) and not re.match(r"^(?:无|无人|不涉及人物|未出现人物|未见人物|画面无人)", value)


def _text_person_reference_language(value: str) -> str:
    return (
        value.replace("目标人物参考图", "已确认人物文字档案")
        .replace("人物参考图", "已确认人物文字档案")
        .replace("人物图片档案", "人物文字档案")
        .replace("人物脸部", "人物形象")
    )


def _enforce_person_replacement_blocks(
    blocks: list[dict], source_shots: list[dict], *, reference_has_image: bool = True,
) -> None:
    """把人物替换写成逐镜硬指令，并消除模型生成的原人物保持冲突。"""
    if len(blocks) != len(source_shots):
        raise ValueError("人物替换镜头数量与时间轴不一致")
    modify_rule, lock_rule = _person_replacement_rules(reference_has_image)
    for block, shot in zip(blocks, source_shots):
        if not _shot_has_person(shot):
            continue
        keep = str(block.get("keep") or "").strip()
        for source in ("保留原人物", "保持原人物", "原视频人物", "参考视频中的人物"):
            keep = keep.replace(source, "目标人物")
        if _PERSON_MOTION_KEEP not in keep:
            keep = f"{_PERSON_MOTION_KEEP}。" if keep in {"", "无", "无。"} else f"{_PERSON_MOTION_KEEP}；{keep}"
        block["keep"] = keep

        modify = str(block.get("modify") or "").strip()
        for person_rule in _PERSON_MODIFY_RULES:
            modify = modify.replace(person_rule, "")
        modify = modify.strip("；。 ")
        block["modify"] = (
            f"{modify_rule}。"
            if modify in {"", "无", "无。"}
            else f"{modify_rule}；{modify}"
        )

        forbid = str(block.get("forbid") or "").strip()
        for person_rule in _PERSON_LOCK_RULES:
            if person_rule != lock_rule and person_rule in forbid:
                forbid = forbid.replace(person_rule, "").strip("；。 ")
        if forbid.count(lock_rule) > 1:
            before, after = forbid.split(lock_rule, 1)
            forbid = (before + lock_rule + after.replace(lock_rule, "")).replace("；；", "；")
        for conflict in ("不得改变人物", "不得更换人物", "不得替换人物", "禁止改变人物", "人物保持不变", "人物不变"):
            forbid = forbid.replace(conflict, lock_rule)
        if lock_rule not in forbid:
            forbid = f"{lock_rule}。" if forbid in {"", "无", "无。"} else f"{lock_rule}；{forbid}"
        block["forbid"] = forbid
        if not reference_has_image:
            for field in _EDIT_BLOCK_FIELDS:
                block[field] = _text_person_reference_language(str(block.get(field) or ""))


def _enforce_external_text_removal(blocks: list[dict], project_mode: str) -> None:
    """包装外文字不属于参考视频格式，逐镜确定性清除而不是交给 GPT 自由决定。"""
    forbid_rule = _forbid_external_text(project_mode)
    for block in blocks:
        delete = str(block.get("delete") or "").strip()
        if _REMOVE_EXTERNAL_TEXT not in delete:
            block["delete"] = (
                f"{_REMOVE_EXTERNAL_TEXT}。"
                if delete in {"", "无", "无。"}
                else f"{_REMOVE_EXTERNAL_TEXT}；{delete}"
            )
        forbid = str(block.get("forbid") or "").strip()
        if forbid_rule not in forbid:
            block["forbid"] = (
                f"{forbid_rule}。"
                if forbid in {"", "无", "无。"}
                else f"{forbid_rule}；{forbid}"
            )


def normalize_full_prompt_contract(
    text: str,
    shot_ranges: list[tuple[float, float]],
    source_shots: list[dict],
    *,
    project_mode: str,
    replace_person: bool,
) -> str:
    """Apply the same server-owned hard rules to AI, manual and preflight paths."""
    document = validate_full_prompt(text, shot_ranges)
    blocks = [
        {"keep": block.keep, "modify": block.modify, "delete": block.delete, "forbid": block.forbid}
        for block in document.blocks
    ]
    if replace_person:
        _enforce_person_replacement_blocks(
            blocks,
            source_shots,
            reference_has_image=_PERSON_REPLACEMENT_PREFIX_MARKER in document.global_prefix,
        )
    _enforce_external_text_removal(blocks, project_mode)
    return _render_structured_edit_prompt(
        deterministic_prefix=document.global_prefix,
        labels=expected_full_prompt_labels(shot_ranges),
        blocks=blocks,
        shot_ranges=shot_ranges,
    )


def _preserve_person_contract_from_source(
    blocks: list[dict], source_blocks: Sequence, *, reference_has_image: bool,
) -> None:
    """Keep per-shot person replacement hard rules across GPT refinements."""
    modify_rule, lock_rule = _person_replacement_rules(reference_has_image)
    for block, source in zip(blocks, source_blocks):
        if not any(rule in source.modify for rule in _PERSON_MODIFY_RULES):
            continue
        modify = str(block.get("modify") or "").strip()
        for stale_rule in _PERSON_MODIFY_RULES:
            modify = modify.replace(stale_rule, "").strip("；。 ")
        block["modify"] = f"{modify_rule}。" if modify in {"", "无", "无。"} else f"{modify_rule}；{modify}"
        forbid = str(block.get("forbid") or "").strip()
        for stale_rule in _PERSON_LOCK_RULES:
            forbid = forbid.replace(stale_rule, "").strip("；。 ")
        for conflict in ("不得改变人物", "不得更换人物", "不得替换人物", "禁止改变人物", "人物保持不变", "人物不变"):
            forbid = forbid.replace(conflict, lock_rule)
        if lock_rule not in forbid:
            forbid = f"{lock_rule}。" if forbid in {"", "无", "无。"} else f"{lock_rule}；{forbid}"
        block["forbid"] = forbid


_EDIT_COLUMN_RE = re.compile(
    r"(?<!\S)(?:[-*#]+\s*)?(?:\d+[.、]\s*)?\*{0,2}(保持|修改|删除|禁止)\*{0,2}"
    r"\s*(?:内容|事项|部分|要求|（[^）\n]*）|\([^\)\n]*\))?\s*[:：]\s*\*{0,2}"
)


def _normalize_edit_block(text: str) -> str | None:
    """兼容旧版自由文本返回；新任务优先使用结构化中间数据。"""
    cleaned = text.replace("\r\n", "\n").replace("```text", "").replace("```", "")
    matches = list(_EDIT_COLUMN_RE.finditer(cleaned))
    values: dict[str, list[str]] = {}
    for index, match in enumerate(matches):
        column = match.group(1)
        if column in values:
            return None
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        body = re.sub(r"\s+", " ", cleaned[match.end():end]).strip().removesuffix("**").strip()
        values[column] = [body] if body else []
    columns = ("保持", "修改", "删除", "禁止")
    if any(not values.get(column) for column in columns):
        return None
    return "\n" + "\n".join(f"{column}：{' '.join(values[column])}" for column in columns)


_EDIT_BLOCK_FIELDS = ("keep", "modify", "delete", "forbid")


def _parse_structured_edit_blocks(text: str, expected_count: int) -> list[dict] | None:
    """解析模型的内容数据；时间标签和中文栏目名不属于模型协议。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        payload = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return None
    blocks = payload.get("blocks") if isinstance(payload, dict) else None
    if not isinstance(blocks, list) or len(blocks) != expected_count:
        return None
    parsed: list[dict] = []
    for block in blocks:
        if not isinstance(block, dict):
            return None
        values = {field: block.get(field) for field in _EDIT_BLOCK_FIELDS}
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            return None
        selling_point_ids = block.get("selling_point_ids", [])
        if not isinstance(selling_point_ids, list) or any(not isinstance(item, str) for item in selling_point_ids):
            return None
        parsed.append({
            **{field: values[field].strip() for field in _EDIT_BLOCK_FIELDS},
            "selling_point_ids": list(dict.fromkeys(item.strip() for item in selling_point_ids if item.strip())),
        })
    return parsed


_SELLING_POINT_ID_RE = re.compile(r"(?m)^(SP\d+)（")


def _validate_selling_point_assignments(blocks: list[dict], deterministic_prefix: str) -> None:
    allowed = set(_SELLING_POINT_ID_RE.findall(deterministic_prefix))
    audio_only = set(re.findall(r"(?m)^(SP\d+)（仅音频", deterministic_prefix))
    counts: dict[str, int] = {}
    for block in blocks:
        ids = block.get("selling_point_ids") or []
        if len(ids) > 1:
            raise ValueError("每个镜头最多分配一个产品卖点")
        for selling_point_id in ids:
            if selling_point_id not in allowed:
                raise ValueError(f"提示词使用了不存在的产品卖点：{selling_point_id}")
            if selling_point_id in audio_only and "允许生成新音频" not in deterministic_prefix:
                raise ValueError(f"当前未开启新音频，不能使用仅音频卖点：{selling_point_id}")
            counts[selling_point_id] = counts.get(selling_point_id, 0) + 1
    repeated = sorted(key for key, count in counts.items() if count > 2)
    if repeated:
        raise ValueError(f"产品卖点在过多镜头中重复：{'、'.join(repeated)}")


def _render_structured_edit_prompt(
    *,
    deterministic_prefix: str,
    labels: list[str],
    blocks: list[dict],
    shot_ranges: list[tuple[float, float]],
) -> str:
    """由服务端独占最终格式的序列化与校验。"""
    if len(labels) != len(blocks):
        raise FullPromptValidationError("结构化镜头数量与时间轴不一致")
    full = deterministic_prefix + "\n\n" + "\n\n".join(
        f"{label}\n保持：{block['keep']}\n修改：{block['modify']}\n删除：{block['delete']}\n禁止：{block['forbid']}"
        for label, block in zip(labels, blocks)
    )
    document = validate_full_prompt(full, shot_ranges)
    return f"{document.global_prefix}\n\n" + "\n\n".join(
        f"{label}\n保持：{block.keep}\n修改：{block.modify}\n删除：{block.delete}\n禁止：{block.forbid}"
        for block, label in zip(document.blocks, labels)
    )


def generate_full_edit_prompt(
    *,
    settings: Settings,
    shot_slices: list[dict],
    deterministic_prefix: str,
    visual_direction: str,
    replace_person: bool = False,
    compatibility_conflicts: list[dict] | None = None,
    project_mode: str = "replace_product",
) -> str:
    """让 GPT 生成栏目内容，由服务端生成时间标签、栏目名和最终格式。"""
    if not settings.comfly_api_key:
        raise VisionConfigurationError("请先在设置中配置 Comfly API Key")
    source_shots = shot_slices
    person_reference_has_image = _PERSON_REPLACEMENT_PREFIX_MARKER in deterministic_prefix
    shot_slices = (
        prepare_person_replacement_shots(shot_slices, reference_has_image=person_reference_has_image)
        if replace_person else shot_slices
    )
    compatibility_conflicts = compatibility_conflicts or []
    shot_ranges = [(slice["start_sec"], slice["end_sec"]) for slice in shot_slices]
    expected_labels = expected_full_prompt_labels(shot_ranges)
    person_replacement_rule = (
        "人物替换已开启：目标人物参考是人物身份与外观的最高优先级；分镜只可保留人数、位置、姿态、动作和表情强度，"
        "不得恢复原人物的性别、年龄、外貌、发型、体型或服装。"
        if replace_person else ""
    )
    video_priority_rule = (
        "参考视频只锁定格式、时间轴、镜头边界、镜头顺序、机位、构图、运镜、动作轨迹、节奏和叙事功能；"
        "已确认的目标人物参考锁定人物身份和整体外观。"
        if replace_person else
        "参考视频只锁定格式、时间轴、镜头边界、镜头顺序、机位、构图、运镜、动作轨迹、节奏和叙事功能。"
    )
    messages = [
        {"role": "system", "content": (
            "你是广告视频编辑指令作者。" + video_priority_rule +
            "只返回一个合法 JSON 对象，格式固定为："
            '{"blocks":[{"keep":"...","modify":"...","delete":"...","forbid":"...","selling_point_ids":[]}]}。'
            "blocks 数量必须与输入分镜数量完全一致，并严格按输入数组顺序对应。四个字段都必须是非空字符串，可填写“无”。"
            "不得返回时间标签、中文栏目名、全局前缀、Markdown、代码围栏或解释文字。"
            "keep 只写与用户改编要求不冲突的弱保持项；modify 先落实用户改编要求，再写产品替换和必要的动作适配；"
            "delete 写原字幕/贴纸/水印等；forbid 只限制用户没有要求改变的内容，绝不能把用户已要求改变的内容重新锁死。"
            "存在SP卖点时，每个镜头最多选择一个自然匹配的SP编号写入selling_point_ids，同一SP最多用于两个镜头；"
            "modify内部按“产品呈现；动作适配；可见结果”组织，但不输出这些内部标签也可以。"
            "抽象成分、品牌、价格和时长不得伪装成画面可见事实；标记为仅音频的卖点只有在全局明确要求新音频时才可使用。"
            "服务端前缀中的【最高优先级：用户改编要求】高于所有镜头事实、参考视频内容和模型补充，必须逐镜落实，"
            "不得遗漏、弱化、反向解释或用 keep/forbid 抵消。未被用户改编要求触及的镜头事实才作为默认值。"
            "输入中列入可适配产品动作的镜头，可按 suggestion 最小调整产品交互，使罐装、盒装或软管产品的交互符合物理逻辑。"
            "人物参考只作为全局锚点；逐镜不得重复展开人物五官、妆容、身材或服装细节。"
            + person_replacement_rule +
            "源视频中的包装外文字永远不继承；每个镜头都必须删除字幕、标题、卖点字、贴纸、角标、水印和平台标识并修复遮挡区域。"
            "不得新增包装外可见文字、Logo、标签、水印、字幕或虚构文案；只允许当前产品实体包装已确认的固有标签。"
            "保留产品模式不得替换、删除或重新设计原产品；替换产品模式必须使用已确认目标产品档案和图片用途名称。"
        )},
        {"role": "user", "content": (
            "服务端确定性全局前缀（只读约束，不得改写，必须严格遵循）：\n"
            f"{deterministic_prefix}\n\n"
            f"改编要求：{visual_direction}\n"
            "可适配产品动作（未列出的镜头不得改动作）：\n"
            f"{json.dumps(compatibility_conflicts, ensure_ascii=False)}\n"
            f"必须返回 {len(shot_slices)} 个 blocks。分镜事实数组：\n"
            + json.dumps(shot_slices, ensure_ascii=False)
        )},
    ]
    for correction_attempt in range(2):
        text = _chat(settings, messages)
        structured_blocks = _parse_structured_edit_blocks(text, len(expected_labels))
        if structured_blocks is None:
            break
        if replace_person:
            _enforce_person_replacement_blocks(
                structured_blocks,
                source_shots,
                reference_has_image=person_reference_has_image,
            )
        _enforce_external_text_removal(structured_blocks, project_mode)
        try:
            _validate_selling_point_assignments(structured_blocks, deterministic_prefix)
            result = _render_structured_edit_prompt(
                deterministic_prefix=deterministic_prefix,
                labels=expected_labels,
                blocks=structured_blocks,
                shot_ranges=shot_ranges,
            )
            if replace_person:
                validate_person_replacement_output(result, source_shots, deterministic_prefix)
        except ValueError as exc:
            if correction_attempt:
                raise
            messages.extend([
                {"role": "assistant", "content": text},
                {"role": "user", "content": (
                    f"上次JSON未通过服务端校验：{exc}。请完整重写同样数量的blocks，并直接修正这个错误。"
                    "每个镜头最多一个SP，同一SP最多用于两个镜头；可以不分配卖点，不要求用完。"
                    "只有selling_point_ids已分配该SP的镜头，modify才可描述该卖点；其他镜头不得换一种说法重复它。"
                    "人物替换已开启时，任何字段都不得恢复原人物的身份、性别、年龄、外貌、发型、体型或服装。"
                    "保持时间顺序、用户改编要求和其他全部约束不变，只返回合法JSON。"
                )},
            ])
            continue
        return result

    # 兼容迁移期间仍返回旧自由文本的模型；最终格式依然由服务端重建。
    body = text
    for label in expected_labels:
        if label in text:
            body = text[text.index(label):]
            break
    full = f"{deterministic_prefix}\n\n{body}"
    try:
        document = validate_full_prompt(full, shot_ranges)
    except FullPromptValidationError:
        # 一次定向补全缺失结构：逐块判定缺失（非全局搜索），并把补全块按时间顺序插入，
        # 避免追加末尾造成乱序。
        body_blocks: dict[str, str] = {}
        for label in expected_labels:
            if label in body:
                block_text = body.split(label, 1)[1]
                next_label = next((other for other in expected_labels if other != label and other in block_text), None)
                block_text = block_text.split(next_label, 1)[0] if next_label else block_text
                normalized = _normalize_edit_block(block_text)
                if normalized:
                    body_blocks[label] = normalized
        for index, label in enumerate(expected_labels):
            if label not in body_blocks:
                repair = _chat(settings, [
                    {"role": "system", "content": (
                        "你负责补全一个镜头的四类内容。只返回合法 JSON："
                        '{"blocks":[{"keep":"...","modify":"...","delete":"...","forbid":"..."}]}。'
                        "四个字段必须是非空字符串；不得返回时间标签、栏目名、Markdown或解释。"
                    )},
                    {"role": "user", "content": (
                        "服务端确定性全局前缀（只读约束，不得改写）：\n"
                        f"{deterministic_prefix}\n\n"
                        f"镜头在时间轴中的范围是 {label}，请只生成内容数据：\n"
                        + json.dumps(shot_slices[index], ensure_ascii=False)
                    )},
                ], max_tokens=4000)
                repair_blocks = _parse_structured_edit_blocks(repair, 1)
                if repair_blocks is not None:
                    block = repair_blocks[0]
                    body_blocks[label] = (
                        f"\n保持：{block['keep']}\n修改：{block['modify']}"
                        f"\n删除：{block['delete']}\n禁止：{block['forbid']}"
                    )
                else:
                    if label in repair:
                        repair = repair.split(label, 1)[1]
                    body_blocks[label] = _normalize_edit_block(repair) or "\n" + repair.lstrip()
        repaired = deterministic_prefix + "\n\n" + "\n\n".join(
            f"{label}{block}" for label, block in ((label, body_blocks[label]) for label in expected_labels)
        )
        document = validate_full_prompt(repaired, shot_ranges)
    fallback_blocks = [
        {"keep": block.keep, "modify": block.modify, "delete": block.delete, "forbid": block.forbid}
        for block in document.blocks
    ]
    if replace_person:
        _enforce_person_replacement_blocks(
            fallback_blocks,
            source_shots,
            reference_has_image=person_reference_has_image,
        )
    _enforce_external_text_removal(fallback_blocks, project_mode)
    result = _render_structured_edit_prompt(
        deterministic_prefix=document.global_prefix,
        labels=expected_labels,
        blocks=fallback_blocks,
        shot_ranges=shot_ranges,
    )
    return validate_person_replacement_output(result, source_shots, deterministic_prefix) if replace_person else result


RECREATION_HEADINGS = ("画面：", "动作：", "镜头：", "光线：", "声音：", "禁止：")


def validate_recreation_prompt(text: str, shot_ranges: list[tuple[float, float]], project_mode: str) -> str:
    labels = expected_full_prompt_labels(shot_ranges)
    label_positions = [text.index(label) if label in text else -1 for label in labels]
    if all(position >= 0 for position in label_positions) and label_positions != sorted(label_positions):
        raise ValueError("独立复刻提示词的镜头时间顺序错误")
    for index, label in enumerate(labels):
        occurrences = text.count(label)
        if occurrences == 0:
            raise ValueError(f"独立复刻提示词缺少镜头：{label}")
        if occurrences != 1:
            raise ValueError(f"独立复刻提示词的镜头时间重复：{label}")
        body = text.split(label, 1)[1]
        next_label = labels[index + 1] if index + 1 < len(labels) else None
        if next_label and next_label in body:
            body = body.split(next_label, 1)[0]
        missing = [heading for heading in RECREATION_HEADINGS if body.count(heading) != 1]
        if missing:
            raise ValueError(f"独立复刻提示词的 {label} 栏目缺失或重复：{', '.join(missing)}")
        positions = [body.index(heading) for heading in RECREATION_HEADINGS]
        if positions != sorted(positions):
            raise ValueError(f"独立复刻提示词的 {label} 栏目顺序错误")
    if any(phrase in text for phrase in ("参考视频", "原视频", "原画面", "保持：", "修改：", "删除：")):
        raise ValueError("独立复刻提示词不能依赖原视频描述")
    if project_mode == "preserve_product" and contains_product_replacement(text):
        raise ValueError("保留产品模式的独立复刻提示词不能替换产品")
    return text.strip()


def _recreation_block(text: str, label: str, labels: list[str]) -> str | None:
    if text.count(label) != 1:
        return None
    start = text.index(label) + len(label)
    ends = [text.index(other, start) for other in labels if other != label and other in text[start:]]
    body = text[start:min(ends) if ends else len(text)].strip()
    if any(body.count(heading) != 1 for heading in RECREATION_HEADINGS):
        return None
    if [body.index(heading) for heading in RECREATION_HEADINGS] != sorted(body.index(heading) for heading in RECREATION_HEADINGS):
        return None
    return f"{label}\n{body}"


def _recreation_prefix(
    *, product_profile: str, person_profile: str, replace_product: bool,
    replace_person: bool, product_purpose_lines: list[str], selling_point_cards: list[dict[str, str]],
    person_reference_has_image: bool = False,
) -> str:
    lines = ["全局一致性：人物、产品、服装、场景、色彩与光线跨镜头连续一致；严格按绝对时间顺序执行全部镜头。"]
    if replace_product:
        lines.append(f"目标产品锁定档案：\n{product_profile}")
        if product_purpose_lines:
            lines.append("目标产品参考图用途：\n" + "\n".join(f"- {item}" for item in product_purpose_lines))
        if selling_point_cards:
            lines.append(
                "可用产品卖点（均为用户提供；只选与现有镜头自然匹配的内容）：\n"
                + render_selling_point_cards(selling_point_cards)
            )
    if replace_person:
        lines.append(
            (
                f"目标人物以参考图为准；文字只锁定关键锚点：\n{person_profile}\n"
                if person_reference_has_image else
                f"已确认人物文字档案是人物身份和整体外观的最高优先级：\n{person_profile}\n"
            )
            + "逐镜不重复展开五官、妆容、身材或服装细节。"
        )
    lines.append("全局声音规则：逐镜严格执行各自的声音栏，禁止增加未明确要求的对白、旁白或营销口播。")
    return "\n".join(lines)


def recreation_audio_instruction(audio_mode: str, audio_style: str) -> str:
    if audio_mode == "none":
        return "每个镜头的声音栏写明不生成音频；不得添加环境音、音乐、对白或旁白。"
    if audio_mode == "auto":
        return "按画面自动设计环境音、产品操作音、动作音和适当背景音乐；不得新增对白、旁白或营销口播。"
    if audio_mode == "custom":
        if not audio_style.strip():
            raise ValueError("请填写音频要求")
        return f"逐镜落实用户音频要求：{audio_style.strip()}；不得添加要求之外的对白、旁白或营销口播。"
    raise ValueError("未知的独立复刻音频选项")


def generate_recreation_prompt(
    *, settings: Settings, shot_slices: list[dict], visual_direction: str,
    product_profile: str, person_profile: str, replace_product: bool,
    replace_person: bool, audio_mode: str, audio_style: str,
    product_purpose_lines: list[str] | None = None,
    compatibility_conflicts: list[dict] | None = None,
    selling_point_cards: list[dict[str, str]] | None = None,
    person_reference_has_image: bool = False,
) -> str:
    """把已确认分镜事实翻译成脱离参考视频仍可使用的完整复刻提示词。"""
    if not settings.comfly_api_key:
        raise VisionConfigurationError("请先在设置中配置 Comfly API Key")
    source_shots = shot_slices
    shot_slices = (
        prepare_person_replacement_shots(shot_slices, reference_has_image=person_reference_has_image)
        if replace_person else shot_slices
    )
    ranges = [(shot["start_sec"], shot["end_sec"]) for shot in shot_slices]
    audio_instruction = recreation_audio_instruction(audio_mode, audio_style)
    product_purpose_lines = product_purpose_lines or []
    compatibility_conflicts = compatibility_conflicts or []
    selling_point_cards = selling_point_cards or []
    source = {
        "required_time_ranges": expected_full_prompt_labels(ranges),
        "confirmed_shots": shot_slices,
        "creative_requirement": visual_direction,
        "replace_product": replace_product,
        "confirmed_product_profile": product_profile if replace_product else "",
        "confirmed_product_image_purposes": product_purpose_lines if replace_product else [],
        "selling_point_cards": selling_point_cards if replace_product else [],
        "replace_person": replace_person,
        "confirmed_person_profile": person_profile if replace_person else "",
        "person_reference_source": "image" if person_reference_has_image else "text",
        "audio_requirement": audio_instruction,
        "adaptable_product_action_conflicts": compatibility_conflicts,
    }
    text = _chat(settings, [
        {"role": "system", "content": (
            "你是视频复刻提示词作者。输入来自逐镜事实，但输出必须是一份完全自包含的中文视频生成提示词，"
            "读者看不到参考视频也能理解每个镜头。不得写原视频、参考视频、保持原内容、替换原内容等依赖性表达。"
            "先写全局一致性要求，再按指定绝对时间范围逐镜输出，不得合并、跳过或改变顺序。"
            "每个镜头必须恰好包含六个栏目：画面、动作、镜头、光线、声音、禁止。"
            "人物首次出现时只写简要身份锚点，后续只写同一人物及其位置，不重复展开外貌；"
            "画面写清产品、场景、前后景和可见文字；动作写清连续过程与产品交互；"
            "镜头要写清景别、机位、构图、运镜、焦点和节奏。替换开关开启时直接写最终人物或产品，不写替换过程。"
            "卖点只分配给自然适合的已有镜头：质地用于产品/取用特写，使用体验用于对应操作，可见效果用于结果或对比镜头；"
            "每个镜头最多承载一个卖点，同一卖点最多出现两次；纯成分、品牌、价格和时长不得伪装成视觉事实。"
            "正常情况下严格遵守全部已确认事实；只有 adaptable_product_action_conflicts 列出的镜头，"
            "才可按 suggestion 最小修改产品交互动作。不得改动时间、人物、场景、产品外观、运镜、光线、可见文字或其他镜头事实。"
            "不得虚构未确认的功效、认证、包装文字、人物动作或场景。只输出最终提示词正文。"
        )},
        {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
    ])
    labels = expected_full_prompt_labels(ranges)
    project_mode = "replace_product" if replace_product else "preserve_product"
    rebuild_all = any(phrase in text for phrase in ("参考视频", "原视频", "原画面", "保持：", "修改：", "删除："))
    if project_mode == "preserve_product" and contains_product_replacement(text):
        rebuild_all = True
    blocks = {label: None if rebuild_all else _recreation_block(text, label, labels) for label in labels}
    for index, label in enumerate(labels):
        if blocks[label] is not None:
            continue
        shot = shot_slices[index]
        shot_id = str(shot.get("shot_id") or "")
        repair = _chat(settings, [
            {"role": "system", "content": (
                "你负责写一个完全自包含的视频生成镜头。必须以指定绝对时间范围开头，"
                "并依次且各一次包含画面、动作、镜头、光线、声音、禁止六个栏目。"
                "不得提到参考视频、原视频、原画面、保持、修改或删除。只输出这个镜头。"
            )},
            {"role": "user", "content": json.dumps({
                "required_time_range": label,
                "confirmed_shot": shot,
                "creative_requirement": visual_direction,
                "replace_product": replace_product,
                "confirmed_product_profile": product_profile if replace_product else "",
                "confirmed_product_image_purposes": product_purpose_lines if replace_product else [],
                "selling_point_cards": selling_point_cards if replace_product else [],
                "replace_person": replace_person,
                "confirmed_person_profile": person_profile if replace_person else "",
                "person_reference_source": "image" if person_reference_has_image else "text",
                "audio_requirement": audio_instruction,
                "adaptable_product_action_conflicts": [
                    item for item in compatibility_conflicts if not shot_id or str(item.get("shot_id") or "") == shot_id
                ],
            }, ensure_ascii=False)},
        ], max_tokens=4000)
        block = _recreation_block(repair, label, [label])
        if block is None:
            raise ValueError(f"GPT 补全结果仍不符合独立复刻镜头结构：{label}")
        validate_recreation_prompt(block, [ranges[index]], project_mode)
        blocks[label] = block
    assembled = _recreation_prefix(
        product_profile=product_profile, person_profile=person_profile,
        replace_product=replace_product, replace_person=replace_person,
        product_purpose_lines=product_purpose_lines,
        selling_point_cards=selling_point_cards,
        person_reference_has_image=person_reference_has_image,
    ) + "\n\n" + "\n\n".join(blocks[label] or "" for label in labels)
    if replace_person and not person_reference_has_image:
        assembled = _text_person_reference_language(assembled)
    result = validate_recreation_prompt(assembled, ranges, project_mode)
    return validate_person_replacement_output(result, source_shots, person_profile) if replace_person else result


def refine_recreation_prompt(
    *, settings: Settings, source_text: str, instruction: str,
    shot_ranges: list[tuple[float, float]], project_mode: str,
) -> str:
    """精修独立复刻提示词，同时冻结时间轴和六栏目结构。"""
    validate_recreation_prompt(source_text, shot_ranges, project_mode)
    labels = expected_full_prompt_labels(shot_ranges)
    revised = _chat(settings, [
        {"role": "system", "content": (
            "你是视频复刻提示词编辑。根据要求修改现有的独立视频复刻提示词，并返回修改后的完整正文。"
            "必须保留全部时间范围、顺序和每个镜头的画面/动作/镜头/光线/声音/禁止六栏目。"
            "输出必须完全自包含，不得出现参考视频、原视频、原画面或保持/修改/删除式编辑指令。"
            "除非用户明确要求，不得虚构功效、认证、包装文字、人物动作或场景。不要解释修改过程。"
        )},
        {"role": "user", "content": (
            f"修改要求：\n{instruction.strip()}\n\n"
            f"必须保留的时间范围：\n{chr(10).join(labels)}\n\n"
            f"当前完整提示词：\n{source_text}"
        )},
    ])
    return validate_recreation_prompt(revised, shot_ranges, project_mode)


def generate_final_prompt(
    *,
    shots: list[dict],
    user_direction: str,
    product_profile: str,
    person_profile: str,
    replace_product: bool,
    replace_person: bool,
    audio_mode: str,
    audio_style: str,
    settings: Settings | None = None,
    segment_slices: list[dict] | None = None,
    product_purpose_lines: list[str] | None = None,
) -> str:
    """只用人工确认事实和用户选择，生成可直接编辑的最终提示词。

    ``segment_slices`` 提供时进入分段增量模式：提示词只表达差异，每个切片
    使用片段内相对时间，固定输出 保持/修改/删除/禁止 四栏目。
    ``product_purpose_lines`` 提供时由服务端确定性写入产品图用途名称。
    """
    settings = settings or Settings()
    if not settings.comfly_api_key:
        raise VisionConfigurationError("请先在设置中配置 Comfly API Key")
    if segment_slices is not None:
        return _generate_segment_edit_prompt(
            slices=segment_slices, user_direction=user_direction,
            product_profile=product_profile, person_profile=person_profile,
            replace_product=replace_product, replace_person=replace_person,
            audio_style=audio_style, audio_mode=audio_mode, settings=settings,
            product_purpose_lines=product_purpose_lines,
        )
    source = {
        "confirmed_shots": shots,
        "user_direction": user_direction,
        "replace_product": replace_product,
        "confirmed_product_profile": product_profile if replace_product else "",
        "replace_person": replace_person,
        "confirmed_person_profile": person_profile if replace_person else "",
        "audio": audio_style if audio_mode == "add_style" else "保持原 BGM，不增加音频描述",
    }
    messages = [
        {"role": "system", "content": "你是广告视频提示词编辑。只能使用输入中的人工确认事实，不得写入模型过程、冲突、置信度或技术说明。必须输出输入中的每一个镜头，不得合并、跳过或提前结束。替换产品为 true 时，每个出现产品的镜头都必须写入已确认目标产品约束；替换人物为 true 时，每个出现人物的镜头都必须写入已确认人物约束。"},
        {"role": "user", "content": (
            f"将以下数据整理成一份完整中文视频生成提示词，共 {len(shots)} 个镜头。先写简短全局要求，再逐镜输出；"
            "每个镜头必须以其 00:00.00–00:03.20 时间范围开头，并保留动作过程、构图运镜、光线风格、画面文字和 keep_unchanged。"
            "仅在对应替换开关为 true 时写入人物或产品替换。只输出完整提示词正文。\n" + json.dumps(source, ensure_ascii=False)
        )},
    ]
    text = _chat(settings, messages)
    labels = [_time_label(shot["start_sec"], shot["end_sec"]) for shot in shots]
    missing = [shot for shot, label in zip(shots, labels) if label not in text]
    repairs: dict[str, str] = {}
    for shot in missing:
        # 每次只补一个镜头，避免模型再次选择性跳过同一批中间镜头。
        label = _time_label(shot["start_sec"], shot["end_sec"])
        repair = _chat(settings, [
            {"role": "system", "content": "你负责补全一个视频镜头提示词。必须以指定时间范围开头，只输出这个镜头，不写开场说明。"},
            {"role": "user", "content": "请根据档案和规则补全 shot，只输出该镜头正文。\n" + json.dumps({
                "shot": shot, "required_time_range": label,
                "user_direction": user_direction, "replace_product": replace_product,
                "confirmed_product_profile": product_profile if replace_product else "",
                "replace_person": replace_person,
                "confirmed_person_profile": person_profile if replace_person else "",
                "audio": source["audio"],
            }, ensure_ascii=False)},
        ], max_tokens=4000)
        if label not in repair:
            raise ValueError(f"GPT 补全结果缺少指定镜头：{label}")
        repairs[label] = repair[repair.index(label):].strip()
    if repairs:
        # 把补出的镜头插回原时间顺序，而不是简单追加到提示词末尾。
        positions = sorted((text.index(label), label) for label in labels if label in text)
        prefix = text[:positions[0][0]].strip() if positions else text.strip()
        segments: dict[str, str] = {}
        for index, (start, label) in enumerate(positions):
            end = positions[index + 1][0] if index + 1 < len(positions) else len(text)
            segments[label] = text[start:end].strip()
        segments.update(repairs)
        text = "\n\n".join(part for part in [prefix, *(segments[label] for label in labels if label in segments)] if part)
    still_missing = [_time_label(shot["start_sec"], shot["end_sec"]) for shot in shots if _time_label(shot["start_sec"], shot["end_sec"]) not in text]
    if still_missing:
        raise ValueError(f"GPT 返回的提示词仍不完整，缺少镜头：{', '.join(still_missing)}")
    if replace_product:
        # 锁定档案由服务端确定性写入，避免模型概括时遗漏用户上传的目标产品。
        return f"目标产品锁定档案（应用于所有含产品镜头）：\n{product_profile}\n\n{text}"
    return text


def _generate_segment_edit_prompt(
    *,
    slices: list[dict],
    user_direction: str,
    product_profile: str,
    person_profile: str,
    replace_product: bool,
    replace_person: bool,
    audio_style: str,
    audio_mode: str,
    settings: Settings,
    product_purpose_lines: list[str] | None = None,
) -> str:
    """分段增量提示词：固定四栏目，服务端确定性骨架，GPT 只填差异。

    ``slices`` 每个元素含 shot_id、source_start/end_sec、relative_start/end_sec。
    ``product_purpose_lines`` 由服务端确定性写入，保留正面/侧面/开口等用途名称。
    """
    global_rules = [
        "原参考视频决定人物、动作、场景、构图、运镜、节奏和镜头顺序。只执行明确修改。",
    ]
    if replace_product:
        # 产品档案由服务端确定性写入，避免模型概括遗漏。
        global_rules.append(f"目标产品必须匹配已确认参考图：\n{product_profile}")
        if product_purpose_lines:
            global_rules.append("产品参考图用途（按名称锁定对应结构，不得省略）：\n" + "\n".join(f"- {line}" for line in product_purpose_lines))
    else:
        # 保留产品模式：明确锁定原产品，而不是仅靠事后文字检测。
        global_rules.append("保持原产品不变，禁止替换、删除或重新设计原产品。")
    if replace_person:
        global_rules.append(f"人物必须匹配已确认人物参考图：\n{person_profile}")
    global_rules.append(user_direction.strip())
    global_rules.append(audio_style if audio_mode == "add_style" else "保持原 BGM，不增加音频描述")
    skeleton = "\n".join(f"全局规则：{rule}" for rule in global_rules if rule)
    labels = [_time_label(slice["relative_start_sec"], slice["relative_end_sec"]) for slice in slices]
    messages = [
        {"role": "system", "content": (
            "你是广告视频编辑指令作者。原参考视频是最高优先级参考，必须保持人物、动作、手部位置、背景、构图、运镜、节奏和镜头顺序。"
            "只输出明确修改。每个镜头必须严格按以下结构输出，不得合并或跳过镜头：\n\n"
            f"00:00.00–00:03.20\n保持：...\n修改：...\n删除：...\n禁止：...\n"
        )},
        {"role": "user", "content": (
            f"为以下片段写编辑指令，共 {len(slices)} 个镜头切片。每个切片用其相对时间范围开头，"
            "四栏目：保持（不可改变内容）、修改（产品替换或局部改编）、删除（原字幕/贴纸/水印等）、禁止（不得新增或改变）。\n"
            + json.dumps({
                "slices": slices, "user_direction": user_direction,
                "replace_product": replace_product,
                "confirmed_product_profile": product_profile if replace_product else "",
                "replace_person": replace_person,
                "confirmed_person_profile": person_profile if replace_person else "",
            }, ensure_ascii=False)
        )},
    ]
    text = _chat(settings, messages)
    text = _validate_segment_edit_prompt(text, labels, slices, settings, user_direction, product_profile, person_profile, replace_product, replace_person)
    return f"{skeleton}\n\n{text}"


def missing_segment_prompt_blocks(text: str, labels: list[str]) -> list[str]:
    """返回缺少（未出现或不含四栏目）的相对时间块标签。

    每个时间块必须包含 保持/修改/删除/禁止 四个栏目，且不能通过全局子串搜索蒙混。
    """
    missing = []
    for label in labels:
        block = text.split(label, 1)[1] if label in text else None
        if block is None:
            missing.append(label)
            continue
        # 截取到下一个时间标签或结尾，检查四栏目。
        next_label = next((other for other in labels if other != label and other in block), None)
        body = block.split(next_label, 1)[0] if next_label else block
        if not all(heading in body for heading in ("保持：", "修改：", "删除：", "禁止：")):
            missing.append(label)
    return missing


def expected_segment_labels(segment: GenerationSegment, shots: list) -> list[str]:
    """从片段与时间轴镜头交集计算预期相对时间标签（数据库为准，不依赖文本）。"""
    labels = []
    for shot in shots:
        overlap_start = max(shot.start_sec, segment.source_start_sec)
        overlap_end = min(shot.end_sec, segment.source_end_sec)
        if overlap_end - overlap_start <= 0.001:
            continue
        labels.append(_time_label(overlap_start - segment.source_start_sec, overlap_end - segment.source_start_sec))
    return labels


def _segment_prefix(text: str, labels: list[str]) -> str:
    """取文本中第一个预期时间标签之前的服务端确定性前缀。"""
    for label in labels:
        if label in text:
            return text[: text.index(label)].strip()
    return ""


def actionable_segment_text(text: str, labels: list[str]) -> str:
    """只连接每个时间块的 修改/删除 正文，供产品替换检测。

    不包含全局锁定规则与 禁止 栏目，避免否定句误伤。
    """
    parts = []
    for label in labels:
        if label not in text:
            continue
        block = text.split(label, 1)[1]
        next_label = next((other for other in labels if other != label and other in block), None)
        body = block.split(next_label, 1)[0] if next_label else block
        # 只取 修改/删除 后的正文（到下一个栏目头为止）。
        for heading in ("修改：", "删除："):
            if heading in body:
                tail = body.split(heading, 1)[1]
                next_heading = next((h for h in ("保持：", "修改：", "删除：", "禁止：") if h != heading and h in tail), None)
                parts.append(tail.split(next_heading, 1)[0] if next_heading else tail)
    return "\n".join(parts)


def _validate_segment_edit_prompt(
    text: str,
    labels: list[str],
    slices: list[dict],
    settings: Settings,
    user_direction: str,
    product_profile: str,
    person_profile: str,
    replace_product: bool,
    replace_person: bool,
) -> str:
    """校验模型输出：每个相对时间标签都出现且含四个栏目，缺则定向补全一次。"""
    missing = missing_segment_prompt_blocks(text, labels)
    if missing:
        # 定向补全一次；再缺则失败，不标记 completed。
        repaired = text
        for label in missing:
            index = slices[labels.index(label)]
            repair = _chat(settings, [
                {"role": "system", "content": "你负责补全一个镜头编辑指令。必须以指定相对时间范围开头，包含 保持/修改/删除/禁止 四栏目，只输出这个镜头。"},
                {"role": "user", "content": "请补全：\n" + json.dumps({
                    "slice": index, "required_time_range": label,
                    "replace_product": replace_product,
                    "confirmed_product_profile": product_profile if replace_product else "",
                    "replace_person": replace_person,
                    "confirmed_person_profile": person_profile if replace_person else "",
                }, ensure_ascii=False)},
            ], max_tokens=4000)
            if label not in repair or not all(h in repair for h in ("保持：", "修改：", "删除：", "禁止：")):
                raise ValueError(f"GPT 补全结果缺少指定镜头编辑指令：{label}")
            repaired += f"\n\n{repair}"
        return repaired
    return text


def refine_prompt(current_prompt: str, instruction: str, settings: Settings | None = None) -> str:
    """按用户追问修改完整提示词，并保留可追溯的新版本。"""
    settings = settings or Settings()
    if not settings.comfly_api_key:
        raise VisionConfigurationError("请先在设置中配置 Comfly API Key")
    labels = list(dict.fromkeys(__import__("re").findall(r"\d{2}:\d{2}\.\d{2}–\d{2}:\d{2}\.\d{2}", current_prompt)))
    revised = _chat(settings, [
        {"role": "system", "content": "你是广告视频提示词编辑。根据用户的新要求修改现有提示词，返回修改后的完整正文。除非用户明确要求改变时间轴，否则必须保留所有镜头时间范围和未被要求改变的约束。不要解释修改过程。"},
        {"role": "user", "content": f"修改要求：\n{instruction.strip()}\n\n当前完整提示词：\n{current_prompt}"},
    ])
    missing = [label for label in labels if label not in revised]
    if missing:
        raise ValueError(f"GPT 修改结果不完整，缺少镜头：{', '.join(missing)}")
    return revised


def transform_full_prompt(
    *,
    settings: Settings,
    source_text: str,
    instruction: str,
    expected_shot_ranges: list[tuple[float, float]],
    required_prefixes: Sequence[str],
    project_mode: str,
    selling_point_context: str = "",
    modify_only: bool = False,
    replace_person: bool = False,
) -> str:
    """精修与卖点优化共用管线；模型只返回内容数据，服务端独占格式。"""
    source_document = validate_full_prompt(source_text, expected_shot_ranges, required_prefixes=required_prefixes)
    source_prefix = source_document.global_prefix
    labels = expected_full_prompt_labels(expected_shot_ranges)
    # 源正文（仅时间块部分）：用第一个预期标签精确定位，避免前缀中的 “00:” 被误切。
    first_label = labels[0] if labels else ""
    source_body = source_text[source_text.index(first_label):].rstrip() if first_label and first_label in source_text else ""
    # 只发送时间块正文给 GPT，避免模型改写前缀。
    revised = _chat(settings, [
        {"role": "system", "content": (
            "你是广告视频编辑指令作者。只允许修改下方每个镜头的 保持/修改/删除/禁止 正文。"
            "只返回一个合法 JSON 对象，格式固定为："
            '{"blocks":[{"keep":"...","modify":"...","delete":"...","forbid":"...","selling_point_ids":[]}]}。'
            f"必须返回 {len(labels)} 个 blocks，严格按输入镜头顺序；每个字段必须是非空字符串，可填写“无”。"
            "不得返回时间标签、中文栏目名、全局前缀、Markdown、代码围栏或解释文字。"
            "不得改变分镜数量或顺序。不得添加字幕、贴纸、浮层或水印。"
            "人物参考只作为全局锚点；逐镜不得重复展开人物五官、妆容、身材或服装细节。"
            "不得虚构用户未提供的功效、认证、成分、数据、包装文字、场景或人物动作。"
            f"{instruction.strip()}"
        )},
        {"role": "user", "content": (
            (f"只读产品与卖点上下文：\n{selling_point_context}\n\n" if selling_point_context else "")
            + f"现有镜头内容（必须全部保留并保持顺序）：\n{source_body}"
        )},
    ])
    structured_blocks = _parse_structured_edit_blocks(revised, len(labels))
    if structured_blocks is not None:
        _validate_selling_point_assignments(structured_blocks, selling_point_context)
        if modify_only:
            for generated, source in zip(structured_blocks, source_document.blocks):
                generated.update(keep=source.keep, delete=source.delete, forbid=source.forbid)
        if replace_person:
            _preserve_person_contract_from_source(
                structured_blocks,
                source_document.blocks,
                reference_has_image=_PERSON_REPLACEMENT_PREFIX_MARKER in source_prefix,
            )
        _enforce_external_text_removal(structured_blocks, project_mode)
        full = _render_structured_edit_prompt(
            deterministic_prefix=source_prefix,
            labels=labels,
            blocks=structured_blocks,
            shot_ranges=expected_shot_ranges,
        )
    elif modify_only:
        raise ValueError("卖点优化没有返回可校验的结构化镜头数据")
    else:
        # 兼容尚未遵循 JSON 协议的旧模型返回。
        body = revised
        for label in labels:
            if label in revised:
                body = revised[revised.index(label):]
                break
        full = f"{source_prefix}\n\n{body}"
        document = validate_full_prompt(full, expected_shot_ranges)
        fallback_blocks = [
            {"keep": block.keep, "modify": block.modify, "delete": block.delete, "forbid": block.forbid}
            for block in document.blocks
        ]
        if replace_person:
            _preserve_person_contract_from_source(
                fallback_blocks,
                source_document.blocks,
                reference_has_image=_PERSON_REPLACEMENT_PREFIX_MARKER in source_prefix,
            )
        _enforce_external_text_removal(fallback_blocks, project_mode)
        full = _render_structured_edit_prompt(
            deterministic_prefix=document.global_prefix,
            labels=labels,
            blocks=fallback_blocks,
            shot_ranges=expected_shot_ranges,
        )
    if project_mode == "preserve_product" and contains_product_replacement(_actionable_bodies_text(full)):
        raise ValueError("保留产品模式的模型输出不能替换产品")
    return full


def _store_prompt_result(session: Session, job: Job, revision: PromptRevision, text: str) -> Job:
    """Persist a provider result only while its task is still active."""
    session.refresh(job)
    if job.status == "cancelled":
        session.refresh(revision)
        return job
    revision.text = text
    revision.status, revision.error_message = "completed", None
    job.status, job.error_message = "completed", None
    session.commit()
    return job


def execute_final_prompt_job(session: Session, job: Job, settings: Settings | None = None) -> Job:
    """在后台生成一个已持久化的提示词版本，避免HTTP请求长时间阻塞。"""
    revision = session.get(PromptRevision, UUID(job.provider_input_id)) if job.provider_input_id else None
    if revision is None or revision.project_id != job.project_id or not revision.source_timeline_revision_id:
        job.status, job.error_message = "failed", "待生成的提示词版本不存在"
        session.commit()
        return job
    shots: list[dict] = []
    segment_slices: list[dict] | None = None
    segment = session.get(GenerationSegment, revision.generation_segment_id) if revision.generation_segment_id else None
    for shot in session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.source_timeline_revision_id).order_by(Shot.position)):
        edit = session.scalar(select(ShotEdit).where(ShotEdit.project_id == job.project_id, ShotEdit.shot_id == shot.id))
        if edit is None or not edit.confirmed:
            raise ValueError(f"镜头 {shot.position + 1} 的事实已失效，请重新确认")
        if segment is not None:
            # 分段模式：只保留与该片段相交的镜头，并记录片段内相对时间。
            overlap_start = max(shot.start_sec, segment.source_start_sec)
            overlap_end = min(shot.end_sec, segment.source_end_sec)
            if overlap_end - overlap_start <= 0.001:
                continue
            segment_slices = segment_slices or []
            segment_slices.append({
                "shot_id": str(shot.id),
                "source_start_sec": overlap_start, "source_end_sec": overlap_end,
                "relative_start_sec": overlap_start - segment.source_start_sec,
                "relative_end_sec": overlap_end - segment.source_start_sec,
                "facts": {"people": edit.people, "action": edit.action, "product": edit.product, "product_interaction": edit.product_interaction, "background": edit.background, "camera": edit.camera, "lighting": edit.lighting, "visual_style": edit.visual_style, "visible_text": edit.visible_text, "uncertainties": edit.uncertainties},
                "changes": {}, "keep": edit.keep_unchanged.splitlines() if edit.keep_unchanged else [],
            })
        else:
            shots.append({"shot_id": str(shot.id), "start_sec": shot.start_sec, "end_sec": shot.end_sec, "facts": {"people": edit.people, "action": edit.action, "product": edit.product, "product_interaction": edit.product_interaction, "background": edit.background, "camera": edit.camera, "lighting": edit.lighting, "visual_style": edit.visual_style, "visible_text": edit.visible_text, "uncertainties": edit.uncertainties}, "changes": {}, "keep": edit.keep_unchanged.splitlines() if edit.keep_unchanged else []})
    frozen_assets: list[Asset] = []
    if revision.reference_asset_ids:
        try:
            frozen_ids = [UUID(value) for value in json.loads(revision.reference_asset_ids)]
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("提示词参考素材快照损坏，请重新生成提示词")
        frozen_assets = [asset for asset_id in frozen_ids if (asset := session.get(Asset, asset_id)) is not None and asset.project_id == job.project_id]
        if len(frozen_assets) != len(frozen_ids):
            raise ValueError("提示词引用的参考素材已不存在，请重新生成提示词")

    def profile(kind: str) -> str:
        assets = [asset for asset in frozen_assets if asset.kind == kind] if revision.reference_asset_ids else list(session.scalars(select(Asset).where(Asset.project_id == job.project_id, Asset.kind == kind).order_by(Asset.id)))
        profiled = [asset for asset in assets if asset.profile_text and asset.profile_text.strip()]
        return "\n\n".join(
            f"{'参考图' if (asset.original_path or '').strip() else '文字档案'} {index + 1}：\n{asset.profile_text.strip()}"
            for index, asset in enumerate(profiled)
        )
    person_reference_assets = (
        [asset for asset in frozen_assets if asset.kind == "person_reference_image"]
        if revision.reference_asset_ids else
        list(session.scalars(select(Asset).where(
            Asset.project_id == job.project_id, Asset.kind == "person_reference_image",
        ).order_by(Asset.id)))
    )
    person_reference_has_image = any((asset.original_path or "").strip() for asset in person_reference_assets)
    project = session.get(Project, job.project_id)
    if project is None:
        raise ValueError("项目不存在")
    product_assets = (
        [asset for asset in frozen_assets if asset.kind in {"product_reference_image", "target_product_reference_image"}]
        if revision.reference_asset_ids
        else confirmed_target_product_assets(session, project)
    )
    product_profile = product_prompt_profile(product_assets) if product_assets and load_structure(product_assets[-1]).get("summary_confirmed") else ""
    selling_point_cards = product_selling_point_cards(product_assets) if product_profile else []
    replace_product = project.mode == "replace_product"
    revision.replace_product = replace_product
    # 服务端确定性写入每张已确认产品图的用途名称与备注，不让 GPT 决定是否保留。
    product_purpose_lines = []
    if replace_product:
        for asset in product_assets:
            structure = load_structure(asset)
            name = str(structure.get("display_name") or structure.get("view_label") or "其他").strip()
            note = str(structure.get("note") or "").strip()
            product_purpose_lines.append(f"{name}：锁定该角度结构" + (f"（{note}）" if note else ""))
    if revision.prompt_mode == "full_reference_video_edit":
        # 完整提示词：使用冻结时间轴全部已确认镜头。
        if not shots:
            raise ValueError("当前时间轴没有已确认镜头")
        # 确定性前缀来自排队时冻结的快照（revision.text），不重新读取当前产品/人物/背景资料。
        frozen_prefix = (revision.text or "").strip()
        if not frozen_prefix:
            raise ValueError("排队版本缺少冻结的确定性前缀")
        deterministic_prefix = frozen_prefix
        _, compatibility_conflicts = check_product_compatibility(product_profile, shots) if replace_product else ("unknown", [])
        blocking_conflicts = [item for item in compatibility_conflicts if item.get("severity") == "blocked"]
        if blocking_conflicts:
            raise ValueError("目标产品与部分镜头动作根本不兼容，请先修改这些镜头")
        generated_text = generate_full_edit_prompt(
            settings=settings,
            shot_slices=shots,
            deterministic_prefix=deterministic_prefix,
            visual_direction=revision.visual_direction,
            replace_person=revision.replace_person,
            compatibility_conflicts=[item for item in compatibility_conflicts if item.get("severity") == "adaptable"],
            project_mode=project.mode,
        )
        if project.mode == "preserve_product":
            if contains_product_replacement(_actionable_bodies_text(generated_text)):
                raise ValueError("保留产品模式的模型输出不能替换产品")
    elif revision.prompt_mode == "standalone_video_recreation":
        if not shots:
            raise ValueError("当前时间轴没有已确认镜头")
        _, compatibility_conflicts = check_product_compatibility(product_profile, shots) if replace_product else ("unknown", [])
        blocking_conflicts = [item for item in compatibility_conflicts if item.get("severity") == "blocked"]
        if blocking_conflicts:
            raise ValueError("目标产品与部分镜头动作根本不兼容，请先修改这些镜头")
        generated_text = generate_recreation_prompt(
            settings=settings, shot_slices=shots, visual_direction=revision.visual_direction,
            product_profile=product_profile, person_profile=profile("person_reference_image"),
            replace_product=replace_product, replace_person=revision.replace_person,
            audio_mode=revision.audio_mode, audio_style=revision.audio_style,
            person_reference_has_image=person_reference_has_image,
            product_purpose_lines=product_purpose_lines,
            compatibility_conflicts=[item for item in compatibility_conflicts if item.get("severity") == "adaptable"],
            selling_point_cards=selling_point_cards,
        )
    elif segment_slices is not None:
        # 分段增量模式：只输出该片段相交镜头，使用片段内相对时间。
        if not segment_slices:
            raise ValueError("生成片段没有覆盖任何已确认镜头")
        generated_text = generate_final_prompt(
            shots=shots, user_direction=revision.visual_direction,
            product_profile=product_profile, person_profile=profile("person_reference_image"),
            replace_product=replace_product, replace_person=revision.replace_person,
            audio_mode=revision.audio_mode, audio_style=revision.audio_style, settings=settings,
            segment_slices=segment_slices, product_purpose_lines=product_purpose_lines,
        )
    else:
        generated_text = generate_final_prompt(
            shots=shots, user_direction=revision.visual_direction,
            product_profile=product_profile, person_profile=profile("person_reference_image"),
            replace_product=replace_product, replace_person=revision.replace_person,
            audio_mode=revision.audio_mode, audio_style=revision.audio_style, settings=settings,
        )
    if project.mode == "preserve_product" and revision.prompt_mode not in {"full_reference_video_edit", "standalone_video_recreation"}:
        # 完整提示词已在 full 分支内用可执行正文检测；此处分段/legacy 只检测可执行正文或全文。
        if segment_slices is not None:
            labels = [_time_label(slice["relative_start_sec"], slice["relative_end_sec"]) for slice in segment_slices]
            if contains_product_replacement(actionable_segment_text(generated_text, labels)):
                raise ValueError("保留产品模式的模型输出不能替换产品")
        elif contains_product_replacement(generated_text):
            raise ValueError("保留产品模式的模型输出不能替换产品")
    return _store_prompt_result(session, job, revision, generated_text)


def execute_prompt_refinement_job(session: Session, job: Job, settings: Settings | None = None) -> Job:
    revision = session.get(PromptRevision, UUID(job.provider_input_id)) if job.provider_input_id else None
    if revision is None or revision.project_id != job.project_id:
        job.status, job.error_message = "failed", "待修改的提示词版本不存在"
        session.commit()
        return job
    project = session.get(Project, job.project_id)
    if project is None:
        raise ValueError("项目不存在")
    source_text = revision.text.strip()
    if not source_text:
        raise ValueError("没有可供修改的完整提示词快照")
    revision.replace_product = project.mode == "replace_product"
    if revision.prompt_mode == "standalone_video_recreation":
        shot_ranges = [(shot.start_sec, shot.end_sec) for shot in session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.source_timeline_revision_id).order_by(Shot.position))]
        refined = refine_recreation_prompt(
            settings=settings,
            source_text=source_text,
            instruction=revision.operation_instruction,
            shot_ranges=shot_ranges,
            project_mode=project.mode,
        )
        return _store_prompt_result(session, job, revision, refined)
    if revision.prompt_mode == "full_reference_video_edit":
        # 完整提示词精修：冻结源前缀，只发块给 GPT，恢复前缀并严格校验。
        shot_ranges = [(shot.start_sec, shot.end_sec) for shot in session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.source_timeline_revision_id).order_by(Shot.position))]
        try:
            transformed = transform_full_prompt(
                settings=settings,
                source_text=source_text,
                instruction=revision.operation_instruction,
                expected_shot_ranges=shot_ranges,
                required_prefixes=("【参考范围】", "【画面文字】"),
                project_mode=project.mode,
                replace_person=revision.replace_person,
            )
        except FullPromptValidationError as exc:
            raise ValueError(f"GPT 精修结果不完整：{exc}") from exc
        return _store_prompt_result(session, job, revision, transformed)
    # legacy/分段模式继续使用 refine_prompt。
    refined_text = refine_prompt(source_text, revision.operation_instruction, settings)
    # 分段编辑指令精修后必须仍覆盖全部预期相对时间块且含四栏目。
    if revision.prompt_mode == "reference_video_edit":
        segment = session.get(GenerationSegment, revision.generation_segment_id) if revision.generation_segment_id else None
        if segment is None:
            raise ValueError("分段编辑指令的生成片段不存在")
        shots = list(session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.source_timeline_revision_id).order_by(Shot.position)))
        expected_labels = expected_segment_labels(segment, shots)
        # 服务端确定性全局规则来自源版本（第一个预期时间标签之前的部分），精修后重新拼接，
        # 保护原产品锁定、目标产品档案、产品图用途、人物参考与音频规则不被 GPT 删除。
        source_prefix = _segment_prefix(source_text, expected_labels)
        if not source_prefix:
            raise ValueError("分段编辑指令缺少服务端全局规则")
        # 丢弃 GPT 返回的全局前缀，只保留其可编辑时间块正文。
        body = refined_text
        for label in expected_labels:
            if label in body:
                body = body[body.index(label):]
                break
        missing = missing_segment_prompt_blocks(body, expected_labels)
        if missing:
            raise ValueError(f"GPT 精修结果不完整，缺少镜头编辑指令：{', '.join(missing)}")
        if project.mode == "preserve_product":
            # 只检测每个时间块的 修改/删除 可执行正文（锁定规则由源前缀确定性重建，无需 GPT 保留）。
            if contains_product_replacement(actionable_segment_text(body, expected_labels)):
                raise ValueError("保留产品模式的模型输出不能替换产品")
        refined_text = f"{source_prefix.rstrip()}\n\n{body.strip()}"
    elif project.mode == "preserve_product" and contains_product_replacement(refined_text):
        raise ValueError("保留产品模式的模型输出不能替换产品")
    return _store_prompt_result(session, job, revision, refined_text)


def execute_selling_point_optimization_job(session: Session, job: Job, settings: Settings | None = None) -> Job:
    """卖点优化：基于冻结源全文，只改时间块正文，恢复源前缀，严格校验。"""
    revision = session.get(PromptRevision, UUID(job.provider_input_id)) if job.provider_input_id else None
    if revision is None or revision.project_id != job.project_id:
        job.status, job.error_message = "failed", "待优化的提示词版本不存在"
        session.commit()
        return job
    project = session.get(Project, job.project_id)
    if project is None:
        raise ValueError("项目不存在")
    source_text = revision.text.strip()
    if not source_text or revision.prompt_mode != "full_reference_video_edit":
        raise ValueError("待优化版本不是完整提示词快照")
    shot_ranges = [(shot.start_sec, shot.end_sec) for shot in session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.source_timeline_revision_id).order_by(Shot.position))]
    if project.mode == "replace_product":
        instruction = (
            "根据已确认产品卖点优化各镜头的产品表现：决定每个卖点最适合出现在哪些已存在镜头；"
            "把抽象卖点转换为可生成的材质、光影、动作结果或产品状态；减少多个镜头对同一卖点的机械重复；"
            "保持前后镜头营销逻辑一致。只可编辑时间块的 修改 栏目。"
            "不得改变时间标签、分镜数量或顺序；不得删除任何栏目；"
            "不得为表现卖点增加不存在的新场景或人物动作；"
            "不得虚构用户未提供的功效、认证、成分、数据或包装文字；"
            "不得添加字幕、卖点贴纸、浮层文字或水印；"
            "不得把无法自然表现的卖点强行塞入所有镜头。"
        )
    else:
        instruction = (
            "优化各镜头对原产品的表现：减少机械重复、提升表现清晰度，保持原产品不变。"
            "只可编辑时间块的 修改 栏目。不得改变时间标签、分镜数量或顺序；不得删除任何栏目；"
            "不得为表现增加不存在的新场景或人物动作；不得虚构功效、认证、成分、数据或包装文字；"
            "不得添加字幕、贴纸、浮层文字或水印。"
        )
    try:
        optimized = transform_full_prompt(
            settings=settings,
            source_text=source_text,
            instruction=instruction,
            expected_shot_ranges=shot_ranges,
            required_prefixes=("【参考范围】", "【画面文字】"),
            project_mode=project.mode,
            selling_point_context=parse_full_prompt(source_text).global_prefix if project.mode == "replace_product" else "",
            modify_only=True,
            replace_person=revision.replace_person,
        )
    except FullPromptValidationError as exc:
        raise ValueError(f"卖点优化结果不完整：{exc}") from exc
    return _store_prompt_result(session, job, revision, optimized)
