import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Asset, Project


def product_assets_for_project(session: Session, project: Project) -> list[Asset]:
    return list(session.scalars(
        select(Asset)
        .where(Asset.project_id == project.id, Asset.kind == "product_reference_image")
        .order_by(Asset.id)
    ))


def confirmed_target_product_assets(session: Session, project: Project) -> list[Asset]:
    if project.mode != "replace_product":
        return []
    return product_assets_for_project(session, project)


def confirmed_generation_product_assets(session: Session, project: Project) -> list[Asset]:
    assets = [
        asset for asset in confirmed_target_product_assets(session, project)
        if asset.profile_text and asset.profile_text.strip() and asset.analysis_status in {"succeeded", "completed"}
    ]
    def is_confirmed(asset: Asset) -> bool:
        try:
            return bool(json.loads(asset.profile_json or "{}").get("summary_confirmed"))
        except (json.JSONDecodeError, AttributeError):
            return False

    if not any(is_confirmed(asset) for asset in assets):
        return []
    return assets


def contains_product_replacement(text: str) -> bool:
    normalized = re.sub(
        r"(?:不得|不能|禁止|不要|不可|不允许).{0,30}?(?:替换|更换|换成|换为|改成|改为|变成|变为|删除|移除)",
        "",
        text,
    )
    lowered = re.sub(
        r"\b(?:do not|don't|must not|never|cannot|can't)\s+(?:replace|swap|change|turn|convert|remove)\b",
        "",
        normalized.lower(),
    )
    english = re.search(
        r"(?:\b(?:replace|swap|change|turn|convert)\b.{0,120}\b(?:product|item|package|packaging|bottle|box|tube|with|for|to|into)\b|\bremove\b.{0,120}\b(?:product|item|package|packaging|bottle|box|tube)\b)",
        lowered,
    )
    chinese = re.search(
        r"(?:把|将)?(?:原有?|当前|这个|视频中(?:的)?)?(?:产品|商品|包装|瓶子|瓶身|盒子|软管).{0,20}(?:替换(?:成|为)?|更换(?:成|为)?|换成|换为|改成|改为|变成|变为|删除|移除)",
        normalized,
    )
    return bool(english or chinese)
