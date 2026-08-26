from __future__ import annotations

from dataclasses import asdict, dataclass
import re


@dataclass
class ProductConflict:
    shot_id: str
    start_sec: float
    end_sec: float
    reason: str
    suggestion: str
    severity: str = "adaptable"


def classify_product(profile: str) -> str | None:
    text = profile.lower()
    explicit = re.search(r"主包装\s*[:：]\s*(罐装|盒装|软管|其他)", text)
    if explicit:
        return {"罐装": "jar", "盒装": "box", "软管": "tube"}.get(explicit.group(1))
    # 档案常包含“禁止生成软管/未见泵头”等排除项；排除项不能反过来决定包装类型。
    text = re.sub(
        r"(?:不得|不能|禁止|不应|不要|不存在|未见|没有|并非|不是).{0,24}?"
        r"(?:软管|罐装|罐子|膏霜罐|盒装|纸盒|牛奶盒|carton|tube)",
        "",
        text,
    )
    if any(word in text for word in ("牛奶盒", "饮品纸盒", "carton")) or ("牛奶" in text and "纸盒" in text):
        return "carton"
    if any(word in text for word in ("软管", "牙膏", "tube")):
        return "tube"
    if any(word in text for word in ("盒装", "纸盒")):
        return "box"
    if any(word in text for word in ("罐装", "罐子", "膏霜罐", "jar")):
        return "jar"
    return None


def _positive_action_text(text: str) -> str:
    """删除“未按压/未喷洒”等否定观察，避免把不存在的动作判为包装冲突。"""
    action_terms = r"按压|泵压|泵头|喷洒|喷出|挤出|挤压|吸管|饮用|直接喝|舀出|掏出|拿取|涂抹|触碰"
    return re.sub(
        rf"(?:未(?:见|观察到|确认|可靠确认)?|没有|并未|无)(?=[^；;。]{{0,60}}(?:{action_terms}))[^；;。]*(?:[；;。]|$)",
        "",
        text,
    )


def check_product_compatibility(profile: str, shots) -> tuple[str, list[dict]]:
    product_kind = classify_product(profile)
    conflicts: list[ProductConflict] = []
    for shot in shots:
        def value(name, default=""):
            if not isinstance(shot, dict):
                return getattr(shot, name, default)
            if name in shot:
                return shot[name]
            return shot.get("facts", {}).get(name, default)

        interaction = _positive_action_text(str(value("product_interaction") or "").lower())
        action = interaction or _positive_action_text(str(value("action") or "").lower())
        if not action:
            continue
        reason = suggestion = ""
        severity = "adaptable"
        if product_kind and interaction and any(word in interaction for word in ("穿上", "穿戴", "套在脚", "系鞋带", "插电", "通电", "充电", "安装到", "驾驶", "坐进")):
            reason = "目标产品属于罐装、盒装或软管包装，原动作无法通过局部产品交互调整而成立。"
            severity = "blocked"
        elif product_kind == "tube" and any(word in action for word in ("喝", "饮用", "掏出", "手抠", "舀出")):
            reason = "软管产品需要通过挤压开口挤出，不能直接饮用或从罐内掏取。"
            suggestion = "改为打开管盖，单手握住软管并轻压管身，将内容物连续挤出。"
        elif product_kind == "carton" and any(word in action for word in ("挤出", "手抠", "掏出", "舀出")):
            reason = "盒装产品通常应拿起、开口或通过吸管饮用，不能按软管挤出或从罐内掏取。"
            suggestion = "改为拿起盒装产品，打开开口或插入吸管后饮用。"
        elif product_kind == "box" and any(word in action for word in ("泵头", "按压", "喷洒", "挤出", "挤压", "吸管", "饮用", "舀出", "掏出")):
            reason = "盒装产品不能直接执行泵压、喷洒、软管挤出、吸管饮用或从罐内取料动作。"
            suggestion = "改为手持、旋转展示盒体或打开盒盖；盒内产品结构未确认时不演示取料。"
        elif product_kind == "jar" and any(word in action for word in ("吸管", "饮用", "直接喝", "挤压软管", "泵头", "泵压", "按压", "喷洒", "喷出", "挤出")):
            reason = "罐装产品应打开罐盖后取用内容物，不能按泵瓶、喷瓶、盒装或软管方式操作。"
            suggestion = (
                "改为旋开罐盖，手持圆罐将开口靠近鼻部轻嗅，保留嗅闻香气的叙事功能。"
                if any(word in action for word in ("嗅闻", "闻香", "靠近鼻", "鼻部"))
                else "改为旋开罐盖，用手指或适合的工具取出少量内容物。"
            )
        if reason:
            shot_id = value("shot_id") or value("id")
            conflicts.append(ProductConflict(
                str(shot_id), float(value("start_sec", 0)), float(value("end_sec", 0)),
                reason, suggestion, severity,
            ))
    return product_kind or "unknown", [asdict(item) for item in conflicts]
