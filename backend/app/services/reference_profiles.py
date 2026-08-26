from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

import requests
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import Asset, Job


PROFILE_PROMPTS = {
    "product_reference_image": """分析产品图片，只记录可见事实，不猜测品牌身份。只返回合法 JSON，不得返回 Markdown 围栏或解释：{"fact_sheet":"","visual_anchor":"","interaction_rules":[],"immutable":[],"uncertainties":[]}。fact_sheet 是详细可编辑中文事实档案，覆盖产品类别、形状比例、包装与开口、颜色材质、Logo与可见文字、当前图片角度、合理互动和不确定项；visual_anchor 只写视频生成必须锁定的外形比例、主辅色、材质表面、开口结构、标签位置和内容物，不超过350字；interaction_rules 只写与真实包装相容的手部互动；immutable 只写不能变化的产品视觉属性。用户明确确认的产品类别和主包装形态优先于单张图片的透视歧义。用户卖点只能作为用户提供信息记录，不能冒充图片可见事实。""",
    "person_reference_image": """分析人物参考图片，只提取生成视频所需的关键人物锚点，不猜测身份。只返回合法 JSON：{\"basic\":\"\",\"hair\":\"\",\"clothing\":\"\",\"distinctive\":\"\"}。basic 只写大致年龄段、可见性别表达及必要的整体体型；hair 只写发色和核心发型；clothing 只写主要服装类型与颜色；distinctive 只写真正显著的配饰或外观特征，没有则写“无”。每个字段使用简短中文短语。禁止逐项描写五官、肤质、妆容、身体部位、服装缝线、未佩戴物品、拍摄姿势、参考图背景或无法确认项；不得输出 Markdown、标题或解释。""",
    "background_reference_image": """分析背景参考图片，只记录可见事实。用中文输出详细可编辑背景档案，覆盖空间类型与风格、桌墙窗及陈设、前中后景、主光方向色温软硬与阴影、景深虚化氛围，以及不希望新增的物体。""",
}
PROFILE_PROMPTS["target_product_reference_image"] = PROFILE_PROMPTS["product_reference_image"]

PRODUCT_PROFILE_KINDS = {"product_reference_image", "target_product_reference_image"}
PACKAGE_FORM_LABELS = {"jar": "罐装", "box": "盒装", "tube": "软管", "other": "其他"}


def _strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        cleaned = "\n".join(lines).strip()
    return cleaned


def parse_product_profile_output(text: str) -> tuple[str, dict]:
    """读取新版结构化产品理解；旧版自由文本仍可直接作为事实档案。"""
    try:
        payload = json.loads(_strip_json_fence(text))
    except (json.JSONDecodeError, TypeError):
        return text.strip(), {}
    if not isinstance(payload, dict) or not isinstance(payload.get("fact_sheet"), str):
        return text.strip(), {}
    fact_sheet = payload["fact_sheet"].strip()
    if not fact_sheet:
        return text.strip(), {}
    updates: dict = {}
    visual_anchor = payload.get("visual_anchor")
    if isinstance(visual_anchor, str) and visual_anchor.strip():
        updates["product_visual_anchor"] = visual_anchor.strip()[:600]
    for source_key, target_key in (
        ("interaction_rules", "product_interaction_rules"),
        ("immutable", "product_immutable"),
        ("uncertainties", "product_uncertainties"),
    ):
        values = payload.get(source_key)
        if isinstance(values, list):
            updates[target_key] = [str(item).strip()[:300] for item in values if str(item).strip()][:20]
    return fact_sheet, updates


def build_selling_point_cards(text: str) -> list[dict[str, str]]:
    """把用户原始卖点变成稳定卡片；不改写或新增营销事实。"""
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    section = "selling_point"
    for raw_line in (text or "").replace("\r", "").split("\n"):
        line = re.sub(r"^[\s#*\-•·]+", "", raw_line)
        line = re.sub(r"^\d+[.、)）]\s*", "", line).strip(" ：:；;")
        if not line:
            continue
        heading = re.match(r"^(?:产品)?核心卖点(?:（([^）]*)）)?\s*[:：]?\s*(.*)$", line)
        if heading:
            section = "usage_experience" if "使用体验" in (heading.group(1) or "") else "selling_point"
            line = heading.group(2).strip()
        else:
            heading = re.match(r"^(成分与配方卖点|使用体验(?:卖点|转化点)?|(?:目标)?人群痛点(?:（[^）]*）)?)\s*[:：]?\s*(.*)$", line)
            if heading:
                title = heading.group(1)
                section = "usage_experience" if "使用体验" in title else "audience_pain" if "人群痛点" in title else "selling_point"
                line = heading.group(2).strip()
        if not line:
            continue
        line = re.sub(r"^(?:成分|配方)\s*[:：]\s*", "", line)
        # 只在明确的新卖点起始词前断句，避免把括号内说明和普通并列短语切碎。
        starts = (
            "双重|四重|高浓度|0硅油|乳霜质地|无需|停留|拒绝|冲水|吹干|"
            "不塌发根|香型|持久留香|可少量|舒蕾|性价比|频繁烫染|想要|天生|"
            "头发受损|发根|通勤"
        )
        line = re.sub(rf"[，,](?=(?:{starts}))", "；", line)
        line = re.sub(rf"(?<=[）)])(?=(?:{starts}))", "；", line)
        line = re.sub(r"(?<=发根)(?=香型)", "；", line)
        for statement in (item.strip(" ，,。") for item in re.split(r"[；;。]+", line)):
            if not statement:
                continue
            # 括号里通常是同一卖点的补充解释；用主句去重并保留首次出现的完整版本。
            dedupe_key = re.sub(r"[\W_]+", "", re.sub(r"[（(][^）)]*[）)]", "", statement).lower())
            if dedupe_key and dedupe_key in seen:
                continue
            if dedupe_key:
                seen.add(dedupe_key)
            audio_only = bool(re.search(
                r"成分|蛋白|神经酰胺|硅油|配方|因子|品牌|国民|性价比|买一送一|促销|价格|"
                r"香型|留香|头皮|无需|停留|\d+\s*(?:分钟|小时|天|元|块|%|折)", statement,
            ))
            candidates.append({
                "claim": statement[:240],
                "source": "user",
                "type": section,
                "presentation": "audio" if audio_only else "visual",
            })
    # 每类先保留最多 6 条，再按原始顺序补到 24 条，避免前面的成分/体验挤掉全部人群痛点。
    selected: set[int] = set()
    section_counts: dict[str, int] = {}
    for index, card in enumerate(candidates):
        card_type = card["type"]
        section_counts[card_type] = section_counts.get(card_type, 0) + 1
        if section_counts[card_type] <= 6:
            selected.add(index)
    for index in range(len(candidates)):
        if len(selected) >= 24:
            break
        selected.add(index)
    return [{"id": f"SP{position + 1}", **candidates[index]} for position, index in enumerate(sorted(selected))]


def product_selling_point_cards(assets: list[Asset]) -> list[dict[str, str]]:
    if not assets:
        return []
    structure = load_structure(assets[-1])
    cards = structure.get("selling_point_cards")
    if isinstance(cards, list):
        normalized = [item for item in cards if isinstance(item, dict) and item.get("id") and item.get("claim")]
        if normalized:
            return normalized
    return build_selling_point_cards(str(structure.get("selling_points") or ""))


def render_selling_point_cards(cards: list[dict[str, str]]) -> str:
    lines = []
    type_labels = {"selling_point": "产品卖点", "usage_experience": "使用体验", "audience_pain": "人群痛点"}
    for card in cards:
        mode = "仅音频" if card.get("presentation") == "audio" else "可视觉表现"
        card_type = type_labels.get(str(card.get("type") or ""), "产品卖点")
        lines.append(f"{card['id']}（{card_type}，{mode}，用户提供）：{str(card['claim']).strip()}")
    return "\n".join(lines)


def product_prompt_profile(assets: list[Asset]) -> str:
    """把详细后台档案渲染为短小、可执行的产品生成锚点。"""
    if not assets:
        return ""
    canonical = assets[-1]
    structure = load_structure(canonical)
    name = str(structure.get("product_name") or "").strip()
    category = str(structure.get("product_category") or "").strip()
    package_form = PACKAGE_FORM_LABELS.get(str(structure.get("package_form") or ""), str(structure.get("package_form") or "").strip())
    visual_anchor = str(structure.get("product_visual_anchor") or "").strip()
    lines = ["；".join(value for value in (f"产品名称：{name}" if name else "", f"产品类别：{category}" if category else "", f"主包装：{package_form}" if package_form else "") if value)]
    if visual_anchor:
        lines.append(f"视觉锚点：{visual_anchor[:600]}")
    else:
        fallback = (canonical.profile_text or "").strip()
        if fallback:
            lines.append(f"已确认产品事实：{fallback[:800]}")
    rules = structure.get("product_interaction_rules")
    if isinstance(rules, list) and rules:
        lines.append("允许互动：" + "；".join(str(item).strip() for item in rules[:8] if str(item).strip()))
    immutable = structure.get("product_immutable")
    if isinstance(immutable, list) and immutable:
        lines.append("必须锁定：" + "；".join(str(item).strip() for item in immutable[:8] if str(item).strip()))
    return "\n".join(line for line in lines if line)


def _is_transient_profile_error(exc: Exception) -> bool:
    """Let the worker retry temporary provider/network failures with backoff."""
    if isinstance(exc, (
        requests.exceptions.ProxyError,
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
    )):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        status_code = exc.response.status_code if exc.response is not None else None
        return status_code is None or status_code in {408, 425, 429} or status_code >= 500
    return False


def _post_chat(settings: Settings, payload: dict) -> requests.Response:
    for attempt in range(3):
        try:
            return requests.post(
                f"{settings.comfly_vision_base_url.rstrip('/')}/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.comfly_api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=120,
            )
        except (
            requests.exceptions.ProxyError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def _response_text(response: requests.Response) -> str:
    """Extract text from the OpenAI-compatible response used by Comfly."""
    payload = response.json()
    return str(payload.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()


def compact_person_profile(text: str) -> str:
    """把结构化视觉结果渲染成简短人物锚点，并区分固定身份与镜头状态。"""
    cleaned = _strip_json_fence(text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("人物理解没有返回规定 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("人物理解返回的不是 JSON 对象")
    values = []
    for field in ("basic", "hair", "clothing", "distinctive"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"人物理解缺少字段：{field}")
        normalized = " ".join(value.split()).strip("，。；; ")
        if normalized not in {"无", "未见", "没有"}:
            values.append(normalized)
    suffix = "。身份、基础发色和体型跨镜头一致；发型、湿干状态、表情和服装按已确认镜头适配，连续场景内保持一致。"
    anchors = "，".join(values)
    available = 140 - len(suffix)
    if len(anchors) > available:
        anchors = anchors[:available].rstrip("，。；; ")
    if not anchors:
        raise ValueError("人物理解没有返回可用锚点")
    return anchors + suffix


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
            f"参考图{index}；角度={metadata.get('display_name') or metadata.get('view_label') or '其他'}；"
            f"人工备注={metadata.get('note') or '无'}\n{raw_profile}"
        )

    canonical = assets[-1]
    canonical_metadata = load_structure(canonical)
    if len(entries) == 1:
        summary = str(canonical_metadata.get("image_profile_text") or canonical.profile_text or "").strip()
    else:
        prompt = f"""请把同一产品的多张参考图理解结果合并。只返回合法 JSON，不得返回 Markdown 围栏或解释：{{"fact_sheet":"","visual_anchor":"","interaction_rules":[],"immutable":[],"uncertainties":[]}}。
只合并证据，不重复逐图叙述，不虚构看不到的结构、功效或品牌身份。
产品名称：{canonical_metadata.get('product_name') or '未填写'}
用户确认产品类别：{canonical_metadata.get('product_category') or '未填写'}
用户确认主包装形态：{PACKAGE_FORM_LABELS.get(str(canonical_metadata.get('package_form') or ''), canonical_metadata.get('package_form') or '未填写')}
用户提供的卖点：{canonical_metadata.get('selling_points') or '未填写'}
卖点必须标明为用户提供信息，不能冒充图片可见事实。
用户明确确认的产品类别、主包装形态及“这些图片属于同一包装”优先于单张图片的透视歧义。

fact_sheet 必须依次包含：产品名称；用户提供卖点；产品类别与用途；外形和比例；包装、开口与盖子；颜色、材质与表面；Logo、包装文字和图案；各角度共同确认的事实；合理产品互动；后续生成必须保持不变；不确定项。
visual_anchor 只保留视频生成必须使用的外形、比例、颜色、材质、开口、标签位置和内容物，不超过350字。

以下是逐图后台结果：
{chr(10).join(entries)}"""
        response = _post_chat(
            settings,
            {
                "model": settings.comfly_vision_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
        )
        response.raise_for_status()
        summary = _response_text(response)
        if not summary:
            raise ValueError("GPT 未返回统一产品事实")
        summary, structure_updates = parse_product_profile_output(summary)
        canonical_metadata.update(structure_updates)

    # 逐图原文保存在 image_profile_text；只有这份统一档案会显示给用户。
    canonical.profile_text = summary
    canonical_metadata.update({
        "selling_point_cards": build_selling_point_cards(str(canonical_metadata.get("selling_points") or "")),
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
                f"\n用户标注：本图角度={metadata.get('display_name') or metadata.get('view_label') or '其他'}；"
                f"单图备注={metadata.get('note') or '无'}；"
                f"产品名称={metadata.get('product_name') or '未填写'}；"
                f"用户确认产品类别={metadata.get('product_category') or '未填写'}；"
                f"用户确认主包装形态={PACKAGE_FORM_LABELS.get(str(metadata.get('package_form') or ''), metadata.get('package_form') or '未填写')}；"
                "用户确认同批上传图片属于同一个包装；"
                f"用户提供的产品卖点={metadata.get('selling_points') or '未填写'}。"
                "卖点只作为用户提供的信息记录，不得伪装成图片可见事实。"
            )
        # 参考图与关键帧复用同一条 Comfly GPT 视觉通道，保存配置后无需重启后端。
        response = _post_chat(
            settings,
            {
                "model": settings.comfly_vision_model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": PROFILE_PROMPTS[asset.kind] + annotation},
                    {"type": "image_url", "image_url": {"url": f"data:{asset.content_type or 'image/jpeg'};base64,{image}"}},
                ]}],
                "temperature": 0.1,
            },
        )
        response.raise_for_status()
        output = _response_text(response)
        if not output:
            raise ValueError("AI 未返回参考素材档案")
        if asset.kind == "person_reference_image":
            output = compact_person_profile(output)
        if asset.kind in PRODUCT_PROFILE_KINDS:
            output, structure_updates = parse_product_profile_output(output)
            metadata.update(structure_updates)
            metadata.update({
                "image_profile_text": output,
                "selling_point_cards": build_selling_point_cards(str(metadata.get("selling_points") or "")),
                "summary_generated": False,
                "summary_confirmed": False,
            })
            asset.profile_json = json.dumps(metadata, ensure_ascii=False)
        asset.profile_text = output
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
        if _is_transient_profile_error(exc):
            # The worker owns attempt counting/backoff. Do not turn a temporary
            # provider disconnect into a terminal asset failure here.
            raise
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
