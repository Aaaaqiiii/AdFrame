from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
from PIL import Image

from app.services.media import MediaToolUnavailableError, _run, clip_video, concat_videos_lossless, detect_candidate_cuts, ensure_image_within_dimensions, ensure_segment_clip, probe_video, validate_reference_duration


def test_missing_media_tool_has_actionable_error(monkeypatch) -> None:
    monkeypatch.setattr("app.services.media.shutil.which", lambda _: None)

    with pytest.raises(MediaToolUnavailableError, match="未找到 ffprobe"):
        _run(["ffprobe", "-version"])


def test_oversized_reference_image_is_normalized_losslessly(tmp_path: Path) -> None:
    source = tmp_path / "wide.jpg"
    destination = tmp_path / "wide.png"
    Image.new("RGB", (7000, 10), "white").save(source, "JPEG")

    result = ensure_image_within_dimensions(source, destination)

    assert result == destination
    with Image.open(result) as image:
        assert image.format == "PNG"
        assert image.width == 6000


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
def test_clip_video_preserves_reference_resolution(tmp_path: Path) -> None:
    source = tmp_path / "portrait.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=720x1440:r=5:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ],
        check=True,
        capture_output=True,
    )

    clip = clip_video(source, tmp_path / "reference-clip.mp4", 0, 1)

    metadata = probe_video(clip)
    assert (metadata.width, metadata.height) == (720, 1440)
    assert metadata.has_audio is False


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


@pytest.mark.media_tools
def test_lossless_concat_preserves_stream_and_duration(tmp_path: Path) -> None:
    clips = []
    for index, color in enumerate(("red", "blue"), start=1):
        target = tmp_path / f"clip-{index}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=96x128:r=10:d=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target),
            ],
            check=True,
            capture_output=True,
        )
        clips.append(target)

    merged = concat_videos_lossless(clips, tmp_path / "merged.mp4")

    first = probe_video(clips[0])
    result = probe_video(merged)
    assert (result.width, result.height, result.video_codec, result.pixel_format) == (
        first.width, first.height, first.video_codec, first.pixel_format,
    )
    assert 1.8 < result.duration_sec < 2.2


@pytest.mark.media_tools
def test_lossless_concat_rejects_incompatible_resolution(tmp_path: Path) -> None:
    clips = []
    for index, size in enumerate(("96x128", "128x128"), start=1):
        target = tmp_path / f"clip-{index}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s={size}:r=10:d=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target),
            ],
            check=True,
            capture_output=True,
        )
        clips.append(target)

    with pytest.raises(ValueError, match="无法无损合并"):
        concat_videos_lossless(clips, tmp_path / "merged.mp4")
