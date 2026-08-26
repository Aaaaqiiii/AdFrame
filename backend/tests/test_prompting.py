from app.services.prompting import compose_prompt


def test_compose_prompt_preserves_shot_facts_and_marks_user_changes() -> None:
    prompt = compose_prompt(
        "original bottle", "", shot_instructions=[{
            "start_sec": 0, "end_sec": 2, "facts": {"action": "holds bottle"},
            "changes": {"background": "warm bathroom"}, "keep": ["product shape"],
        }],
    )

    assert "0.00–2.00 秒：" in prompt
    assert "原视频事实：action为 holds bottle。" in prompt
    assert "用户修改：background改为 warm bathroom。" in prompt
    assert "保持不变：product shape。" in prompt


def test_prompt_uses_chinese_timeline_and_product_locking() -> None:
    text = compose_prompt("白色包装盒，蓝色文字", "人物改为短发女性", shot_instructions=[{
        "start_sec": 0, "end_sec": 3,
        "facts": {"action": "拿起产品", "background": "桌面"},
        "changes": {"people": "短发女性"}, "keep": ["产品外观", "包装文字"],
    }])

    assert "严格保持参考视频的完整分镜" in text
    assert "不得替换、删除或改变原产品身份" in text
from fastapi.testclient import TestClient
from sqlalchemy import select
from uuid import UUID

from app.db.models import PromptRevision
from app.db.session import SessionLocal
from app.main import create_app


def test_keep_original_bgm_adds_no_audio_instruction() -> None:
    prompt = compose_prompt(
        product_profile="透明精华瓶，银色瓶盖",
        visual_direction="清晨窗边的产品特写",
        audio_mode="keep_original",
    )

    assert "音频" not in prompt
    assert "背景音乐" not in prompt


def test_audio_style_is_added_only_when_selected() -> None:
    prompt = compose_prompt(
        product_profile="透明精华瓶，银色瓶盖",
        visual_direction="清晨窗边的产品特写",
        audio_mode="add_style",
        audio_style="轻盈钢琴",
    )

    assert "轻盈钢琴" in prompt


def test_prompt_revisions_are_saved_with_audio_choice() -> None:
    from unittest.mock import patch
    from app.services.media import VideoMetadata
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "提示词"}).json()
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
    prefix = build_full_prompt_prefix(
        project_mode="preserve_product", product_profile="", product_image_purposes=[],
        people_reference=None, background_reference=None,
        audio_mode="add_style", audio_style="轻盈钢琴",
    )
    text = prefix + "\n\n" + "00:00.00–00:03.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。"
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={
            "product_profile": "透明精华瓶，银色瓶盖",
            "visual_direction": text,
            "audio_mode": "add_style",
            "audio_style": "轻盈钢琴",
            "use_ai": False,
        },
    )

    assert response.status_code == 201
    assert response.json()["version"] == 1
    assert response.json()["status"] == "completed"
    assert "完整移除原片全部包装外字幕" in response.json()["text"]
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(PromptRevision.project_id == UUID(project["id"])))
        assert revision.audio_mode == "custom"
        assert revision.audio_style == "轻盈钢琴"
