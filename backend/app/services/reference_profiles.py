from __future__ import annotations

import base64
import json
from pathlib import Path

import requests
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import Asset, Job


PROFILE_PROMPTS = {
    "product_reference_image": """分析产品图片，只记录可见事实，不猜测品牌身份。用中文输出详细可编辑档案，覆盖：产品类别和用途；整体形状与尺寸比例；包装结构、开口、盖子和密封方式；主辅色、材质及反光；Logo位置、可见文字和图案；正侧背面或使用状态；合理手部互动；绝对不能变化的属性；无法确认项。先输出一段完整档案，再在最后输出一行 JSON：{\"category\":\"\",\"shape\":\"\",\"opening\":\"\",\"interaction\":\"\",\"immutable\":[]}。""",
    "person_reference_image": """分析人物参考图片，只记录可见外观，不猜测身份。用中文输出详细可编辑人物档案，覆盖年龄段、可见性别表达、脸型五官、肤色妆容表情、发色发型长度碎发、身材体态、服装颜色材质版型、饰品和无法确认项。""",
    "background_reference_image": """分析背景参考图片，只记录可见事实。用中文输出详细可编辑背景档案，覆盖空间类型与风格、桌墙窗及陈设、前中后景、主光方向色温软硬与阴影、景深虚化氛围，以及不希望新增的物体。""",
}
PROFILE_PROMPTS["target_product_reference_image"] = PROFILE_PROMPTS["product_reference_image"]

PRODUCT_PROFILE_KINDS = {"product_reference_image", "target_product_reference_image"}


def _response_text(response: requests.Response) -> str:
    """Extract text from the OpenAI-compatible response used by Comfly."""
    payload = response.json()
    return str(payload.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()


def _consolidate_product_profiles(session, asset: Asset, settings: Settings) -> None:
    """Create one editable product fact sheet after every selected image has finished."""
    assets = list(session.scalars(select(Asset).where(
        Asset.project_id == asset.project_id,
        Asset.kind == asset.kind,
    ).order_by(Asset.id)))
    if not assets or any(item.analysis_status != "succeeded" for item in assets):
        return

    entries: list[str] = []
    for index, item in enumerate(assets, 1):
        metadata = load_structure(item)
        raw_profile = str(metadata.get("image_profile_text") or item.profile_text or "").strip()
        entries.append(
            f"参考图{index}；角度={metadata.get('view_label') or '其他'}；"
            f"人工备注={metadata.get('note') or '无'}\n{raw_profile}"
        )

    canonical = assets[-1]
    canonical_metadata = load_structure(canonical)
    if len(entries) == 1:
        summary = str(canonical_metadata.get("image_profile_text") or canonical.profile_text or "").strip()
    else:
        prompt = f"""请把同一产品的多张参考图理解结果合并成一份中文产品事实档案。
只合并证据，不重复逐图叙述，不虚构看不到的结构、功效或品牌身份。
产品名称：{canonical_metadata.get('product_name') or '未填写'}
用户提供的卖点：{canonical_metadata.get('selling_points') or '未填写'}
卖点必须标明为用户提供信息，不能冒充图片可见事实。

统一档案必须依次包含：产品名称；用户提供卖点；产品类别与用途；外形和比例；包装、开口与盖子；颜色、材质与表面；Logo、包装文字和图案；各角度共同确认的事实；合理产品互动；后续生成必须保持不变；不确定项。

以下是逐图后台结果：
{chr(10).join(entries)}"""
        response = requests.post(
            f"{settings.comfly_vision_base_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.comfly_api_key}", "Content-Type": "application/json"},
            json={
                "model": settings.comfly_vision_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
            timeout=120,
        )
        response.raise_for_status()
        summary = _response_text(response)
        if not summary:
            raise ValueError("GPT 未返回统一产品事实")

    # 逐图原文保存在 image_profile_text；只有这份统一档案会显示给用户。
    canonical.profile_text = summary
    canonical_metadata.update({
        "summary_generated": True,
        "summary_confirmed": False,
        "summary_error": "",
    })
    canonical.profile_json = json.dumps(canonical_metadata, ensure_ascii=False)


def queue_profile_job(session, asset: Asset, *, retry: bool = False, settings: Settings | None = None) -> Job | None:
    settings = settings or Settings()
    asset.profile_user_edited = False
    if not settings.comfly_api_key:
        asset.analysis_status = "failed"
        asset.analysis_error = "服务器未配置 Comfly API Key，图片已保存；配置后可点击重试理解。"
        session.commit()
        return None
    if retry:
        for existing in session.query(Job).filter(
            Job.project_id == asset.project_id,
            Job.kind == "reference_profile_analysis",
            Job.provider_input_id == str(asset.id),
            Job.status.in_(["queued", "processing", "retryable"]),
        ):
            return existing
    asset.analysis_status, asset.analysis_error = "queued", None
    job = Job(
        project_id=asset.project_id,
        kind="reference_profile_analysis",
        status="queued",
        provider="comfly_gpt_vision",
        provider_input_id=str(asset.id),
    )
    session.add(job)
    session.commit()
    return job


def execute_profile_job(session, job: Job, settings: Settings | None = None) -> Job:
    settings = settings or Settings()
    asset = session.get(Asset, job.provider_input_id) if job.provider_input_id else None
    if asset is None or asset.kind not in PROFILE_PROMPTS:
        job.status, job.error_message = "failed", "待理解的参考图片不存在"
        session.commit()
        return job
    if not settings.comfly_api_key:
        message = "服务器未配置 Comfly API Key，无法理解参考图片"
        job.status, job.error_message = "failed", message
        asset.analysis_status, asset.analysis_error = "failed", message
        session.commit()
        return job
    asset.analysis_status, job.status = "running", "processing"
    session.commit()
    try:
        image = base64.b64encode(Path(asset.original_path).read_bytes()).decode("ascii")
        metadata = load_structure(asset)
        annotation = ""
        if asset.kind in {"product_reference_image", "target_product_reference_image"}:
            annotation = (
                f"\n用户标注：本图角度={metadata.get('view_label') or '其他'}；"
                f"单图备注={metadata.get('note') or '无'}；"
                f"产品名称={metadata.get('product_name') or '未填写'}；"
                f"用户提供的产品卖点={metadata.get('selling_points') or '未填写'}。"
                "卖点只作为用户提供的信息记录，不得伪装成图片可见事实。"
            )
        # 参考图与关键帧复用同一条 Comfly GPT 视觉通道，保存配置后无需重启后端。
        response = requests.post(
            f"{settings.comfly_vision_base_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.comfly_api_key}", "Content-Type": "application/json"},
            json={
                "model": settings.comfly_vision_model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": PROFILE_PROMPTS[asset.kind] + annotation},
                    {"type": "image_url", "image_url": {"url": f"data:{asset.content_type or 'image/jpeg'};base64,{image}"}},
                ]}],
                "temperature": 0.1,
            },
            timeout=120,
        )
        response.raise_for_status()
        output = _response_text(response)
        if not output:
            raise ValueError("AI 未返回参考素材档案")
        asset.profile_text = output
        if asset.kind in PRODUCT_PROFILE_KINDS:
            metadata.update({
                "image_profile_text": output,
                "summary_generated": False,
                "summary_confirmed": False,
            })
            asset.profile_json = json.dumps(metadata, ensure_ascii=False)
        asset.analysis_status, asset.analysis_error = "succeeded", None
        job.status, job.error_message = "completed", None
        session.flush()
        if asset.kind in PRODUCT_PROFILE_KINDS:
            try:
                _consolidate_product_profiles(session, asset, settings)
            except Exception as exc:
                # 汇总失败不抹掉逐图结果，界面仍可显示事实并允许人工填写。
                metadata = load_structure(asset)
                metadata["summary_error"] = f"统一产品事实生成失败：{exc}"
                asset.profile_json = json.dumps(metadata, ensure_ascii=False)
    except Exception as exc:
        message = f"参考图片理解失败：{exc}"
        asset.analysis_status, asset.analysis_error = "failed", message
        job.status, job.error_message = "failed", message
    session.commit()
    return job


def load_structure(asset: Asset) -> dict:
    if not asset.profile_json:
        return {}
    try:
        value = json.loads(asset.profile_json)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}
