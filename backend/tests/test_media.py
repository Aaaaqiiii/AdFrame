from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from app.services.media import MediaToolUnavailableError, _run, clip_video, detect_candidate_cuts, ensure_segment_clip, probe_video, validate_reference_duration


def test_missing_media_tool_has_actionable_error(monkeypatch) -> None:
    monkeypatch.setattr("app.services.media.shutil.which", lambda _: None)

    with pytest.raises(MediaToolUnavailableError, match="未找到 ffprobe"):
        _run(["ffprobe", "-version"])


@pytest.fixture()
def video_31_seconds(tmp_path: Path) -> Path:
    target = tmp_path / "long.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=5:d=31",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target),
        ],
        check=True,
        capture_output=True,
    )
    return target


@pytest.fixture()
def video_with_cut(tmp_path: Path) -> Path:
    target = tmp_path / "cut.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=5:d=5",
            "-f", "lavfi", "-i", "color=c=white:s=64x64:r=5:d=5",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(target),
        ],
        check=True,
        capture_output=True,
    )
    return target


@pytest.mark.media_tools
def test_reference_duration_over_30_blocks_submission_only(video_31_seconds: Path) -> None:
    metadata = probe_video(video_31_seconds)

    assert validate_reference_duration(metadata)[0].code == "reference_video_too_long"


@pytest.mark.media_tools
def test_candidate_cuts_are_real_timestamps(video_with_cut: Path) -> None:
    assert any(4.8 < cut < 5.2 for cut in detect_candidate_cuts(video_with_cut))


@pytest.mark.media_tools
def test_clip_video_exports_only_the_requested_shot(video_with_cut: Path, tmp_path: Path) -> None:
    clip = clip_video(video_with_cut, tmp_path / "shot.mp4", 5, 8)

    assert 2.8 < probe_video(clip).duration_sec < 3.2


@pytest.mark.media_tools
def test_corrupt_segment_cache_is_rebuilt(video_with_cut: Path, tmp_path: Path) -> None:
    """已存在但损坏的裁片缓存必须被删除并重新裁切（clip_video 恰好调用一次）。"""
    source = video_with_cut
    destination = tmp_path / "cache.mp4"
    # 写一个损坏的缓存文件（非视频内容）。
    destination.write_bytes(b"corrupt-cache-result")
    with patch("app.services.media.clip_video", wraps=clip_video) as wrapped:
        result = ensure_segment_clip(source, destination, 5, 8, 29.0)
        # 损坏缓存被重建：clip_video 调用一次。
        assert wrapped.call_count == 1
    assert 2.8 < probe_video(result).duration_sec < 3.2


@pytest.mark.media_tools
def test_valid_segment_cache_is_reused(video_with_cut: Path, tmp_path: Path) -> None:
    """存在且有效的裁片缓存必须复用，不重新裁切。"""
    source = video_with_cut
    destination = tmp_path / "valid.mp4"
    clip_video(source, destination, 5, 8)
    with patch("app.services.media.clip_video", wraps=clip_video) as wrapped:
        result = ensure_segment_clip(source, destination, 5, 8, 29.0)
        assert wrapped.call_count == 0
    assert result == destination
