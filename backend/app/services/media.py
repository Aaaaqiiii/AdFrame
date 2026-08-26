from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps


@dataclass(frozen=True)
class VideoMetadata:
    duration_sec: float
    width: int
    height: int
    fps: float
    has_audio: bool = False
    video_codec: str = ""
    pixel_format: str = ""
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    audio_channel_layout: str | None = None


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class EvidenceFrame:
    timestamp_sec: float
    path: Path


class MediaToolUnavailableError(RuntimeError):
    pass


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(command[0])
    if not executable:
        raise MediaToolUnavailableError(f"服务器未找到 {command[0]}，请安装 FFmpeg 并重启服务")
    try:
        return subprocess.run([executable, *command[1:]], check=True, capture_output=True, text=True)
    except PermissionError as exc:
        raise MediaToolUnavailableError(f"服务器无权运行 {command[0]}，请检查 FFmpeg 安装权限并重启服务") from exc


def _fps(value: str) -> float:
    numerator, denominator = value.split("/", 1)
    return float(numerator) / float(denominator) if float(denominator) else 0.0


def probe_video(path: Path) -> VideoMetadata:
    result = _run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,sample_rate,channel_layout",
        "-of", "json", str(path),
    ])
    payload = json.loads(result.stdout)
    stream = next(item for item in payload["streams"] if item["codec_type"] == "video")
    audio_stream = next((item for item in payload["streams"] if item["codec_type"] == "audio"), None)
    return VideoMetadata(
        duration_sec=float(payload["format"]["duration"]),
        width=int(stream["width"]),
        height=int(stream["height"]),
        fps=_fps(stream["avg_frame_rate"]),
        has_audio=audio_stream is not None,
        video_codec=str(stream.get("codec_name") or ""),
        pixel_format=str(stream.get("pix_fmt") or ""),
        audio_codec=str(audio_stream.get("codec_name") or "") if audio_stream else None,
        audio_sample_rate=int(audio_stream["sample_rate"]) if audio_stream and audio_stream.get("sample_rate") else None,
        audio_channel_layout=str(audio_stream.get("channel_layout") or "") if audio_stream else None,
    )


def concat_videos_lossless(sources: list[Path], destination: Path) -> Path:
    """Join compatible MP4 clips by stream copy; never re-encode or reduce quality."""
    if not sources:
        raise ValueError("没有可合并的视频片段")
    missing = [path for path in sources if not path.is_file()]
    if missing:
        raise ValueError("部分生成片段文件不存在")
    metadata = [probe_video(path) for path in sources]
    first = metadata[0]
    for index, item in enumerate(metadata[1:], start=2):
        same_video = (
            item.width == first.width
            and item.height == first.height
            and abs(item.fps - first.fps) <= 0.01
            and item.video_codec == first.video_codec
            and item.pixel_format == first.pixel_format
        )
        same_audio = (
            item.has_audio == first.has_audio
            and item.audio_codec == first.audio_codec
            and item.audio_sample_rate == first.audio_sample_rate
            and item.audio_channel_layout == first.audio_channel_layout
        )
        if not same_video or not same_audio:
            raise ValueError(f"片段 {index} 的编码、分辨率、帧率或音频轨道与前一片段不一致，无法无损合并")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = destination.with_name(f".{destination.stem}-{uuid4().hex}.part{destination.suffix}")
    list_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".concat.txt", prefix="merge-",
            dir=destination.parent, delete=False,
        ) as concat_file:
            list_path = Path(concat_file.name)
            for source in sources:
                escaped = source.resolve().as_posix().replace("'", "'\\''")
                concat_file.write(f"file '{escaped}'\n")
        _run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy", "-movflags", "+faststart", str(temporary_output),
        ])
        merged = probe_video(temporary_output)
        expected_duration = sum(item.duration_sec for item in metadata)
        duration_tolerance = max(0.5, len(metadata) * 0.1)
        same_output = (
            temporary_output.stat().st_size > 0
            and merged.width == first.width
            and merged.height == first.height
            and merged.video_codec == first.video_codec
            and merged.pixel_format == first.pixel_format
            and merged.has_audio == first.has_audio
            and merged.audio_codec == first.audio_codec
            and merged.audio_sample_rate == first.audio_sample_rate
            and merged.audio_channel_layout == first.audio_channel_layout
            and abs(merged.duration_sec - expected_duration) <= duration_tolerance
        )
        if not same_output:
            raise RuntimeError("无损合并结果校验失败")
        temporary_output.replace(destination)
        return destination
    finally:
        temporary_output.unlink(missing_ok=True)
        if list_path is not None:
            list_path.unlink(missing_ok=True)


def probe_image_dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def ensure_image_within_dimensions(source: Path, destination: Path, max_dimension: int = 6000) -> Path:
    width, height = probe_image_dimensions(source)
    if width <= max_dimension and height <= max_dimension:
        return source
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file():
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            image.save(destination, "PNG", optimize=True)
    normalized_width, normalized_height = probe_image_dimensions(destination)
    if normalized_width > max_dimension or normalized_height > max_dimension:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Seedance reference image normalization failed")
    return destination


def validate_reference_duration(metadata: VideoMetadata) -> list[ValidationIssue]:
    if metadata.duration_sec > 30:
        return [ValidationIssue("reference_video_too_long", "参考视频超过 30 秒；仍可生成提示词，但提交生成前建议缩短或调整。")]
    return []


def extract_keyframes(source: Path, destination: Path, timestamps: list[float]) -> list[EvidenceFrame]:
    destination.mkdir(parents=True, exist_ok=True)
    frames: list[EvidenceFrame] = []
    for index, timestamp in enumerate(timestamps):
        target = destination / f"frame-{index + 1:03d}.jpg"
        _run(["ffmpeg", "-y", "-ss", str(timestamp), "-i", str(source), "-frames:v", "1", "-vf", "scale=1280:-2:force_original_aspect_ratio=decrease", "-q:v", "3", str(target)])
        frames.append(EvidenceFrame(timestamp, target))
    return frames


def clip_video(source: Path, destination: Path, start_sec: float, end_sec: float) -> Path:
    """Create a self-contained visual-analysis clip for one confirmed shot."""
    if end_sec <= start_sec:
        raise ValueError("Shot end must be after shot start")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-ss", str(start_sec), "-i", str(source), "-t", str(end_sec - start_sec),
        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destination),
    ])
    probe_video(destination)
    return destination


def ensure_segment_clip(source: Path, destination: Path, start_sec: float, end_sec: float, max_sec: float) -> Path:
    """Create or reuse a compact persistent segment clip.

    - 缓存不存在：正常裁切。
    - 缓存可探测且与源视频分辨率一致：直接复用。
    - 缓存分辨率与源视频不一致：删除并按原始分辨率重建，禁止降低参考清晰度。
    - 缓存探测显示损坏/未写完整：删除并重新裁切。
    - FFmpeg/FFprobe 工具不可用：保留原异常，不误删文件。
    """
    def _probe(path: Path) -> VideoMetadata:
        try:
            return probe_video(path)
        except MediaToolUnavailableError:
            # 工具缺失时原样抛出，绝不删除缓存。
            raise
        except (subprocess.CalledProcessError, json.JSONDecodeError, StopIteration, KeyError, ValueError):
            # 媒体损坏或解析失败：删除坏缓存并重新裁切。
            path.unlink(missing_ok=True)
            raise

    source_metadata = probe_video(source)
    if destination.is_file():
        try:
            metadata = _probe(destination)
            if (metadata.width, metadata.height) != (source_metadata.width, source_metadata.height):
                destination.unlink()
                metadata = _probe(clip_video(source, destination, start_sec, end_sec))
        except (subprocess.CalledProcessError, json.JSONDecodeError, StopIteration, KeyError, ValueError):
            # 损坏缓存已删除，回退到全新裁切。
            metadata = _probe(clip_video(source, destination, start_sec, end_sec))
    else:
        metadata = _probe(clip_video(source, destination, start_sec, end_sec))
    if metadata.duration_sec > max_sec:
        destination.unlink(missing_ok=True)
        raise ValueError("生成片段超过供应商安全时长")
    return destination


def detect_candidate_cuts(source: Path) -> list[float]:
    result = _run([
        "ffmpeg", "-i", str(source), "-vf", "select='gt(scene,0.30)',showinfo",
        "-f", "null", "-",
    ])
    return [float(value) for value in re.findall(r"pts_time:([0-9.]+)", result.stderr)]
