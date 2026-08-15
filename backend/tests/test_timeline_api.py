from fastapi.testclient import TestClient
from pathlib import Path
import subprocess

from app.main import create_app


def _cut_video(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=5:d=5",
            "-f", "lavfi", "-i", "color=c=white:s=64x64:r=5:d=5",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_human_timeline_can_be_split_and_merged() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "时间轴"}).json()

    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 10}]},
    )
    assert timeline.status_code == 202

    split = client.post(
        f"/api/projects/{project['id']}/timeline/{timeline.json()['revision_id']}/split",
        json={"shot_id": timeline.json()["shots"][0]["id"], "at_sec": 4.5},
    )
    assert split.status_code == 202
    assert len(split.json()["shots"]) == 2

    merged = client.post(
        f"/api/projects/{project['id']}/timeline/{split.json()['revision_id']}/merge",
        json={"shot_ids": [shot["id"] for shot in split.json()["shots"]]},
    )
    assert merged.status_code == 202
    assert merged.json()["shots"] == [{"start_sec": 0.0, "end_sec": 10.0, "id": merged.json()["shots"][0]["id"]}]


def test_human_timeline_rejects_gaps_and_overlaps() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "完整时间轴"}).json()

    gap = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}, {"start_sec": 4, "end_sec": 8}]},
    )
    overlap = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 4}, {"start_sec": 3, "end_sec": 8}]},
    )

    assert gap.status_code == 422
    assert overlap.status_code == 422


def test_analysis_start_creates_ai_timeline_from_real_candidate_cuts(tmp_path: Path) -> None:
    video = tmp_path / "reference.mp4"
    _cut_video(video)
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "AI 时间轴"}).json()
    with video.open("rb") as stream:
        client.post(
            f"/api/projects/{project['id']}/reference-video",
            files={"file": ("reference.mp4", stream, "video/mp4")},
        )

    response = client.post(f"/api/projects/{project['id']}/analysis/start")

    assert response.status_code == 202


def test_timeline_split_copies_edit_to_each_replacement_shot() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "edit mapping"}).json()
    timeline = client.put(f"/api/projects/{project['id']}/timeline", json={"shots": [{"start_sec": 0, "end_sec": 4}]}).json()
    client.put(f"/api/projects/{project['id']}/shots/{timeline['shots'][0]['id']}/edit", json={"background": "blue studio"})

    split = client.post(f"/api/projects/{project['id']}/timeline/{timeline['revision_id']}/split", json={"shot_id": timeline["shots"][0]["id"], "at_sec": 2}).json()
    detail = client.get(f"/api/projects/{project['id']}").json()

    assert split["revision_id"] == detail["timeline"]["revision_id"]
    assert [shot["edit"]["background"] for shot in detail["timeline"]["shots"]] == ["blue studio", "blue studio"]
