from __future__ import annotations

import json
import time
from uuid import UUID

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.volcengine_vision import VisionConfigurationError
from app.db.models import Asset, GenerationSegment, Job, Project, PromptRevision, Shot, ShotEdit
from app.services.product_rules import confirmed_target_product_assets, contains_product_replacement
from app.services.reference_profiles import load_structure


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
            requests.exceptions.Timeout,
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
            shots.append({"start_sec": shot.start_sec, "end_sec": shot.end_sec, "facts": {"people": edit.people, "action": edit.action, "product": edit.product, "product_interaction": edit.product_interaction, "background": edit.background, "camera": edit.camera, "lighting": edit.lighting, "visual_style": edit.visual_style, "visible_text": edit.visible_text, "uncertainties": edit.uncertainties}, "changes": {}, "keep": edit.keep_unchanged.splitlines() if edit.keep_unchanged else []})
    def profile(kind: str) -> str:
        assets = session.scalars(select(Asset).where(Asset.project_id == job.project_id, Asset.kind == kind).order_by(Asset.id)).all()
        profiles = [asset.profile_text.strip() for asset in assets if asset.profile_text and asset.profile_text.strip()]
        return "\n\n".join(f"参考图 {index + 1}：\n{value}" for index, value in enumerate(profiles))
    project = session.get(Project, job.project_id)
    if project is None:
        raise ValueError("项目不存在")
    product_assets = confirmed_target_product_assets(session, project)
    product_profile = product_assets[-1].profile_text.strip() if product_assets and load_structure(product_assets[-1]).get("summary_confirmed") and product_assets[-1].profile_text else ""
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
    if segment_slices is not None:
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
    if project.mode == "preserve_product":
        if segment_slices is not None:
            # 分段模式：只检测每个时间块的 修改/删除 可执行正文，不检测全局锁定规则与 禁止 栏目。
            labels = [_time_label(slice["relative_start_sec"], slice["relative_end_sec"]) for slice in segment_slices]
            if contains_product_replacement(actionable_segment_text(generated_text, labels)):
                raise ValueError("保留产品模式的模型输出不能替换产品")
        elif contains_product_replacement(generated_text):
            raise ValueError("保留产品模式的模型输出不能替换产品")
    revision.text = generated_text
    revision.status, revision.error_message = "completed", None
    job.status, job.error_message = "completed", None
    session.commit()
    return job


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
    refined_text = refine_prompt(source_text, revision.visual_direction, settings)
    revision.replace_product = project.mode == "replace_product"
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
    revision.text = refined_text
    revision.status, revision.error_message = "completed", None
    job.status, job.error_message = "completed", None
    session.commit()
    return job
