from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import requests
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Asset, Job, PromptRevision
from app.db.session import SessionLocal
from app.main import create_app
from app.services.final_prompt import generate_final_prompt, refine_prompt
from app.services.media import VideoMetadata
from app.services.reference_profiles import (
    _consolidate_product_profiles,
    _is_transient_profile_error,
    _post_chat,
    build_selling_point_cards,
    compact_person_profile,
    load_structure,
    parse_product_profile_output,
    product_prompt_profile,
)


def test_generated_replacement_prompt_always_contains_product_lock() -> None:
    """即使模型正文只写镜头内容，服务端也必须保留完整目标产品档案。"""
    with patch("app.services.final_prompt.requests.post") as post:
        post.return_value.json.return_value = {
            "choices": [{"message": {"content": "00:00.00–00:03.20\n人物拿起产品"}}],
        }
        result = generate_final_prompt(
            shots=[],
            user_direction="保持节奏",
            product_profile="深绿色圆柱瓶，黑色泵头，白色 Logo",
            person_profile="",
            replace_product=True,
            replace_person=False,
            audio_mode="keep_original",
            audio_style="",
            settings=type("Settings", (), {
                "comfly_api_key": "configured",
                "comfly_vision_base_url": "https://example.invalid",
                "comfly_vision_model": "test-model",
            })(),
        )

    assert result.startswith("目标产品锁定档案")
    assert "深绿色圆柱瓶，黑色泵头，白色 Logo" in result


def test_long_prompt_repairs_missing_shots_instead_of_saving_partial_output() -> None:
    """第一次返回不完整时，只补缺失镜头，最终结果必须覆盖完整时间轴。"""
    first = MagicMock()
    first.json.return_value = {"choices": [{"message": {"content": "全局要求\n00:00.00–00:02.50\n镜头一\n\n00:06.53–00:08.50\n镜头三"}}]}
    second = MagicMock()
    second.json.return_value = {"choices": [{"message": {"content": "00:02.50–00:06.53\n镜头二"}}]}
    with patch("app.services.final_prompt.requests.post", side_effect=[first, second]):
        result = generate_final_prompt(
            shots=[{"start_sec": 0, "end_sec": 2.5}, {"start_sec": 2.5, "end_sec": 6.53}, {"start_sec": 6.53, "end_sec": 8.5}],
            user_direction="保持时间轴", product_profile="", person_profile="",
            replace_product=False, replace_person=False, audio_mode="keep_original", audio_style="",
            settings=type("Settings", (), {"comfly_api_key": "configured", "comfly_vision_base_url": "https://example.invalid", "comfly_vision_model": "gpt-5.6-terra"})(),
        )
    assert "00:00.00–00:02.50" in result
    assert "00:02.50–00:06.53" in result
    assert "00:06.53–00:08.50" in result
    assert result.index("00:00.00–00:02.50") < result.index("00:02.50–00:06.53") < result.index("00:06.53–00:08.50")


def test_prompt_refinement_returns_complete_new_text() -> None:
    current = "00:00.00–00:02.50\n原提示词"
    with patch("app.services.final_prompt.requests.post") as post:
        post.return_value.json.return_value = {"choices": [{"message": {"content": "00:00.00–00:02.50\n修改后的完整提示词"}}]}
        result = refine_prompt(current, "加强产品包装描述", settings=type("Settings", (), {"comfly_api_key": "configured", "comfly_vision_base_url": "https://example.invalid", "comfly_vision_model": "gpt-5.6-terra"})())
    assert result.endswith("修改后的完整提示词")


def test_prompt_request_retries_a_transient_proxy_disconnect() -> None:
    """Comfly 临时断开代理连接时，只重试当前请求，不重跑整条提示词任务。"""
    success = MagicMock()
    success.json.return_value = {
        "choices": [{"message": {"content": "00:00.00–00:02.50\n重试后成功"}}],
    }

    with (
        patch(
            "app.services.final_prompt.requests.post",
            side_effect=[requests.exceptions.ProxyError("temporary disconnect"), success],
        ) as post,
        patch("app.services.final_prompt.time.sleep") as sleep,
    ):
        result = refine_prompt(
            "00:00.00–00:02.50\n原提示词",
            "保持结构并润色",
            settings=type(
                "Settings",
                (),
                {
                    "comfly_api_key": "configured",
                    "comfly_vision_base_url": "https://example.invalid",
                    "comfly_vision_model": "gpt-5.6-terra",
                },
            )(),
        )

    assert result.endswith("重试后成功")
    assert post.call_count == 2
    sleep.assert_called_once_with(1)


def test_reference_profile_request_retries_a_transient_proxy_disconnect() -> None:
    success = MagicMock()
    settings = type("Settings", (), {
        "comfly_api_key": "configured",
        "comfly_vision_base_url": "https://example.invalid",
    })()

    with (
        patch(
            "app.services.reference_profiles.requests.post",
            side_effect=[requests.exceptions.ProxyError("temporary disconnect"), success],
        ) as post,
        patch("app.services.reference_profiles.time.sleep") as sleep,
    ):
        assert _post_chat(settings, {"model": "test-model", "messages": []}) is success

    assert post.call_count == 2
    sleep.assert_called_once_with(1)


def test_reference_profile_job_only_retries_transient_provider_errors() -> None:
    retryable_response = requests.Response()
    retryable_response.status_code = 503
    retryable = requests.exceptions.HTTPError(response=retryable_response)
    terminal_response = requests.Response()
    terminal_response.status_code = 400
    terminal = requests.exceptions.HTTPError(response=terminal_response)

    assert _is_transient_profile_error(requests.exceptions.ProxyError("disconnect")) is True
    assert _is_transient_profile_error(retryable) is True
    assert _is_transient_profile_error(terminal) is False
    assert _is_transient_profile_error(ValueError("bad payload")) is False


def test_person_profile_is_one_compact_server_rendered_anchor() -> None:
    raw = """```json
{"basic":"年轻成年女性，身形修长","hair":"黑色中高丸子头","clothing":"灰褐色短袖上衣与同色长裤","distinctive":"无"}
```"""

    profile = compact_person_profile(raw)

    assert profile == (
        "年轻成年女性，身形修长，黑色中高丸子头，灰褐色短袖上衣与同色长裤。"
        "身份、基础发色和体型跨镜头一致；发型、湿干状态、表情和服装按已确认镜头适配，连续场景内保持一致。"
    )
    assert len(profile) <= 140
    assert "五官" not in profile
    assert len(compact_person_profile(
        '{"basic":"年轻女性","hair":"黑色长发","clothing":"' + "灰" * 200 + '","distinctive":"无"}'
    )) == 140


def test_product_profile_separates_editable_facts_from_runtime_anchor() -> None:
    fact_sheet, structure = parse_product_profile_output('''```json
{"fact_sheet":"同一发膜罐的正面、背面和开口图。","visual_anchor":"低矮圆形乳白罐，圆盖，开口后可见白色膏体。","interaction_rules":["可手持、旋转、开盖和取用膏体"],"immutable":["罐体比例与标签位置不变"],"uncertainties":[]}
```''')
    asset = Asset(
        kind="product_reference_image", original_path="unused.png",
        profile_text=fact_sheet,
        profile_json=__import__("json").dumps({
            "product_name": "舒蕾发膜", "product_category": "发膜", "package_form": "jar",
            **structure,
        }, ensure_ascii=False),
    )

    runtime = product_prompt_profile([asset])

    assert fact_sheet == "同一发膜罐的正面、背面和开口图。"
    assert "产品名称：舒蕾发膜；产品类别：发膜；主包装：罐装" in runtime
    assert "视觉锚点：低矮圆形乳白罐" in runtime
    assert "允许互动：可手持、旋转、开盖和取用膏体" in runtime
    assert "同一发膜罐的正面" not in runtime


def test_selling_point_cards_keep_claims_and_mark_non_visual_claims() -> None:
    cards = build_selling_point_cards("产品核心卖点：\n白色膏体质地轻盈；72小时长效顺滑；双重蛋白配方")

    assert [card["claim"] for card in cards] == ["白色膏体质地轻盈", "72小时长效顺滑", "双重蛋白配方"]
    assert cards[0]["presentation"] == "visual"
    assert cards[1]["presentation"] == "audio"
    assert cards[2]["presentation"] == "audio"


def test_selling_point_cards_deduplicate_and_preserve_audience_pains() -> None:
    cards = build_selling_point_cards("""产品核心卖点：
0硅油配方（不闷头皮，不压塌发根）；双重蛋白配方；四重神经酰胺复配
核心卖点（使用体验转化点）
乳霜质地；冲水不滑腻；吹干自然水光；不塌发根；香型高级；持久留香；0硅油配方
人群痛点（转化点）
频繁烫染导致干枯分叉；天生粗硬炸毛；发根容易扁塌；通勤人群没有时间去理发店护理""")

    assert sum("0硅油" in card["claim"] for card in cards) == 1
    assert any(card["type"] == "audience_pain" and "干枯分叉" in card["claim"] for card in cards)
    assert any(card["type"] == "audience_pain" and "扁塌" in card["claim"] for card in cards)
    assert len(cards) <= 24
    assert [card["id"] for card in cards] == [f"SP{index}" for index in range(1, len(cards) + 1)]


def test_person_profile_can_be_created_without_an_image() -> None:
    """没有人物照片时，纯文字档案也必须持久化并可恢复。"""
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "text-person", "mode": "preserve_product"}).json()
    description = "25岁左右女性，黑色齐肩直发，白色衬衫，自然妆容，亲和微笑"

    saved = client.put(
        f"/api/projects/{project['id']}/reference-profiles/person",
        json={"profile": description},
    )
    assert saved.status_code == 200
    assert saved.json()["status"] == "succeeded"

    details = client.get(f"/api/projects/{project['id']}").json()
    assert details["person_reference_image_name"] is None
    assert details["person_profile"] == description

    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}]},
    ).json()
    saved_shot = client.put(
        f"/api/projects/{project['id']}/shots/{timeline['shots'][0]['id']}/edit",
        json={"people": "原视频男性出镜", "action": "正面展示", "confirmed": True},
    )
    assert saved_shot.status_code == 200

    prompt = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "替换人物", "replace_person": True, "use_ai": True},
    )
    assert prompt.status_code == 202
    assert "已确认人物文字档案是人物身份和整体外观的最高优先级" in prompt.json()["text"]
    assert "人物参考图" not in prompt.json()["text"]


def test_saving_multi_image_profile_returns_the_combined_failure_status() -> None:
    """Saving the latest view must not hide a failed sibling behind a success badge."""
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "combined status", "mode": "replace_product"}).json()
    with SessionLocal() as session:
        session.add(Asset(
            project_id=UUID(project["id"]), kind="product_reference_image",
            original_path="failed-front.png", original_filename="front.png",
            analysis_status="failed", analysis_error="参考图片理解失败：temporary disconnect",
            profile_json='{"view_label":"front","display_name":"正面"}',
        ))
        session.add(Asset(
            project_id=UUID(project["id"]), kind="product_reference_image",
            original_path="back.png", original_filename="back.png",
            analysis_status="succeeded", profile_text="背面可见事实",
            profile_json='{"view_label":"back","display_name":"背面"}',
        ))
        session.commit()

    saved = client.put(
        f"/api/projects/{project['id']}/product-profile",
        json={"profile": "人工确认的产品事实", "structure": {"summary_confirmed": True}},
    )

    assert saved.status_code == 200
    assert saved.json()["status"] == "failed"
    assert "temporary disconnect" in saved.json()["error"]


def test_page_two_collects_product_images_and_forces_replacement() -> None:
    """页面二的产品多图必须属于同一项目，且替换规则不能被请求关闭。"""
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "page-two", "mode": "replace_product"}).json()

    with patch("app.api.routes.projects.Settings") as settings:
        settings.return_value.media_root = Path("E:/工具-商用/data/test-media")
        settings.return_value.openai_api_key = "configured"
        for name, view in (("front.png", "front"), ("side.png", "right")):
            response = client.post(
                f"/api/projects/{project['id']}/reference-images/product",
                data={
                    "view_label": view, "note": f"{view} view",
                    "product_name": "测试产品", "product_category": "发膜",
                    "package_form": "jar", "selling_points": "清晰展示包装",
                },
                files={"file": (name, b"stored-image", "image/png")},
            )
            assert response.status_code == 202

    with SessionLocal() as session:
        assets = session.scalars(select(Asset).where(
            Asset.project_id == UUID(project["id"]),
            Asset.kind == "product_reference_image",
        ).order_by(Asset.id)).all()
        assert len(assets) == 2
        for index, asset in enumerate(assets, 1):
            asset.profile_text = f"目标产品视角 {index}"
            asset.analysis_status = "succeeded"
        session.commit()

    details = client.get(f"/api/projects/{project['id']}").json()
    assert len(details["product_reference_images"]) == 2
    assert "目标产品视角 1" in details["product_profile"]
    assert "目标产品视角 2" in details["product_profile"]
    assert details["product_reference_images"][0]["view_label"] in {"front", "right"}

    confirmed = client.put(
        f"/api/projects/{project['id']}/product-profile",
        json={"profile": details["product_profile"], "structure": {
            "product_name": "测试产品", "product_category": "发膜",
            "package_form": "jar", "selling_points": "清晰展示包装",
            "summary_confirmed": True,
        }},
    )
    assert confirmed.status_code == 200

    with patch("app.api.routes.projects.probe_video", return_value=VideoMetadata(3.0, 1280, 720, 30)):
        client.post(
            f"/api/projects/{project['id']}/reference-video",
            files={"file": ("reference.mp4", b"video-bytes", "video/mp4")},
        )
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}]},
    ).json()
    client.put(
        f"/api/projects/{project['id']}/shots/{timeline['shots'][0]['id']}/edit",
        json={"action": "展示", "confirmed": True},
    )
    from app.services.final_prompt import build_full_prompt_prefix
    from app.services.reference_profiles import product_prompt_profile, product_selling_point_cards
    with SessionLocal() as session:
        assets = session.scalars(select(Asset).where(
            Asset.project_id == UUID(project["id"]), Asset.kind == "product_reference_image",
        ).order_by(Asset.id)).all()
        product_profile = product_prompt_profile(assets)
        selling_point_cards = product_selling_point_cards(assets)
        purposes = []
        for asset in assets:
            structure = load_structure(asset)
            name = str(structure.get("display_name") or structure.get("view_label") or "其他").strip()
            note = str(structure.get("note") or "").strip()
            purposes.append(f"{name}：锁定该角度结构" + (f"（{note}）" if note else ""))
    full_text = (
        build_full_prompt_prefix(
            project_mode="replace_product", product_profile=product_profile,
            product_image_purposes=purposes,
            selling_point_cards=selling_point_cards,
            people_reference=None, background_reference=None,
            audio_mode="keep_original", audio_style="",
        ) + "\n\n"
        "00:00.00–00:03.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。"
    )
    saved = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": full_text, "replace_product": False, "use_ai": False},
    )
    assert saved.status_code == 201
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
        ).order_by(PromptRevision.version.desc()))
        assert revision is not None and revision.replace_product is True


def test_product_image_display_name_is_persisted_without_reanalysis() -> None:
    """修改单张产品图名称不得重跑分析，并且刷新项目后必须恢复。"""
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "image labels", "mode": "replace_product"}).json()
    other_project = client.post("/api/projects", json={"name": "other project", "mode": "replace_product"}).json()
    with SessionLocal() as session:
        asset = Asset(
            project_id=UUID(project["id"]), kind="product_reference_image",
            original_path="unused-front.png", original_filename="front.png",
            analysis_status="succeeded", profile_text="已完成的图片事实",
            profile_json='{"view_label":"other","note":"保留备注"}',
        )
        session.add(asset)
        session.commit()
        asset_id = str(asset.id)
        jobs_before = len(session.scalars(select(Job).where(Job.project_id == UUID(project["id"]))).all())

    response = client.patch(
        f"/api/projects/{project['id']}/reference-images/product/{asset_id}",
        json={"view_label": "front", "display_name": "  瓶身正面  "},
    )

    assert response.status_code == 200
    assert response.json()["view_label"] == "front"
    assert response.json()["display_name"] == "瓶身正面"
    details = client.get(f"/api/projects/{project['id']}").json()
    assert details["product_reference_images"][0]["display_name"] == "瓶身正面"
    assert "参考图 1（瓶身正面" in details["product_profile"]
    with SessionLocal() as session:
        saved = session.get(Asset, UUID(asset_id))
        assert saved is not None and saved.analysis_status == "succeeded"
        assert load_structure(saved)["note"] == "保留备注"
        assert len(session.scalars(select(Job).where(Job.project_id == UUID(project["id"]))).all()) == jobs_before

    assert client.patch(
        f"/api/projects/{project['id']}/reference-images/product/{asset_id}",
        json={"view_label": "front", "display_name": "   "},
    ).status_code == 422
    assert client.patch(
        f"/api/projects/{project['id']}/reference-images/product/{asset_id}",
        json={"view_label": "front", "display_name": "超" * 41},
    ).status_code == 422
    assert client.patch(
        f"/api/projects/{other_project['id']}/reference-images/product/{asset_id}",
        json={"view_label": "front", "display_name": "其他项目图片"},
    ).status_code == 404


def test_multiple_product_images_generate_one_background_summary() -> None:
    """多张图片的逐图原文继续保留，但页面读取一份统一产品事实。"""
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "product-summary", "mode": "replace_product"}).json()
    with SessionLocal() as session:
        for index, view in enumerate(("front", "back"), 1):
            session.add(Asset(
                project_id=UUID(project["id"]), kind="product_reference_image",
                original_path=f"unused-{index}.png", original_filename=f"{view}.png",
                analysis_status="succeeded", profile_text=f"逐图事实 {index}",
                profile_json='{"view_label":"%s","product_name":"测试产品","selling_points":"用户卖点","image_profile_text":"逐图事实 %s"}' % (view, index),
            ))
        session.commit()
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": "统一后的产品事实"}}]}
        with patch("app.services.reference_profiles.requests.post", return_value=response):
            asset = session.scalar(select(Asset).where(
                Asset.project_id == UUID(project["id"]), Asset.kind == "product_reference_image",
            ))
            _consolidate_product_profiles(session, asset, type("Settings", (), {
                "comfly_api_key": "configured", "comfly_vision_base_url": "https://example.invalid",
                "comfly_vision_model": "gpt-5.6-terra",
            })())
            session.commit()
        assets = session.scalars(select(Asset).where(
            Asset.project_id == UUID(project["id"]), Asset.kind == "product_reference_image",
        ).order_by(Asset.id)).all()
        canonical = assets[-1]
        assert canonical.profile_text == "统一后的产品事实"
        assert load_structure(canonical)["summary_generated"] is True
        assert load_structure(canonical)["image_profile_text"].startswith("逐图事实")
