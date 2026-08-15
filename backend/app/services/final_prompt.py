from __future__ import annotations

import json
import time
from uuid import UUID

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.volcengine_vision import VisionConfigurationError
from app.db.models import Asset, Job, Project, PromptRevision, Shot, ShotEdit
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
) -> str:
    """只用人工确认事实和用户选择，生成可直接编辑的最终提示词。"""
    settings = settings or Settings()
    if not settings.comfly_api_key:
        raise VisionConfigurationError("请先在设置中配置 Comfly API Key")
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
    for shot in session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.source_timeline_revision_id).order_by(Shot.position)):
        edit = session.scalar(select(ShotEdit).where(ShotEdit.project_id == job.project_id, ShotEdit.shot_id == shot.id))
        if edit is None or not edit.confirmed:
            raise ValueError(f"镜头 {shot.position + 1} 的事实已失效，请重新确认")
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
    revision.text = generate_final_prompt(
        shots=shots, user_direction=revision.visual_direction,
        product_profile=product_profile, person_profile=profile("person_reference_image"),
        replace_product=replace_product, replace_person=revision.replace_person,
        audio_mode=revision.audio_mode, audio_style=revision.audio_style, settings=settings,
    )
    if project.mode == "preserve_product" and contains_product_replacement(revision.text):
        raise ValueError("保留产品模式的模型输出不能替换产品")
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
    source = session.scalar(select(PromptRevision).where(
        PromptRevision.project_id == job.project_id,
        PromptRevision.version < revision.version,
        PromptRevision.status == "completed",
        PromptRevision.text != "",
    ).order_by(PromptRevision.version.desc()))
    if source is None:
        raise ValueError("没有可供修改的完整提示词")
    revision.text = refine_prompt(source.text, revision.visual_direction, settings)
    revision.replace_product = project.mode == "replace_product"
    if project.mode == "preserve_product" and contains_product_replacement(revision.text):
        raise ValueError("保留产品模式的模型输出不能替换产品")
    revision.status, revision.error_message = "completed", None
    job.status, job.error_message = "completed", None
    session.commit()
    return job
