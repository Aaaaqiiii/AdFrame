from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class ProductConflict:
    shot_id: str
    start_sec: float
    end_sec: float
    reason: str
    suggestion: str


def classify_product(profile: str) -> str | None:
    text = profile.lower()
    if any(word in text for word in ("软管", "牙膏", "挤压", "tube")):
        return "tube"
    if any(word in text for word in ("盒装", "纸盒", "牛奶盒", "carton")):
        return "carton"
    if any(word in text for word in ("罐装", "罐子", "膏霜罐", "jar")):
        return "jar"
    return None


def check_product_compatibility(profile: str, shots) -> tuple[str, list[dict]]:
    product_kind = classify_product(profile)
    conflicts: list[ProductConflict] = []
    for shot in shots:
        action = " ".join(filter(None, (shot.product_interaction, shot.action))).lower()
        if not action:
            continue
        reason = suggestion = ""
        if product_kind == "tube" and any(word in action for word in ("喝", "饮用", "掏出", "手抠", "舀出")):
            reason = "软管产品需要通过挤压开口挤出，不能直接饮用或从罐内掏取。"
            suggestion = "改为打开管盖，单手握住软管并轻压管身，将内容物连续挤出。"
        elif product_kind == "carton" and any(word in action for word in ("挤出", "手抠", "掏出", "舀出")):
            reason = "盒装产品通常应拿起、开口或通过吸管饮用，不能按软管挤出或从罐内掏取。"
            suggestion = "改为拿起盒装产品，打开开口或插入吸管后饮用。"
        elif product_kind == "jar" and any(word in action for word in ("吸管", "饮用", "直接喝", "挤压软管")):
            reason = "罐装产品应打开罐盖后取用内容物，不能按盒装饮用或挤压软管。"
            suggestion = "改为旋开罐盖，用手指或适合的工具取出少量内容物。"
        if reason:
            conflicts.append(ProductConflict(str(shot.id), shot.start_sec, shot.end_sec, reason, suggestion))
    return product_kind or "unknown", [asdict(item) for item in conflicts]
