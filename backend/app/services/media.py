from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoMetadata:
    duration_sec: float
    width: int
    height: int
    fps: float
    has_audio: bool = False


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
        "format=duration:stream=codec_type,width,height,avg_frame_rate",
        "-of", "json", str(path),
    ])
    payload = json.loads(result.stdout)
    stream = next(item for item in payload["streams"] if item["codec_type"] == "video")
    return VideoMetadata(
        duration_sec=float(payload["format"]["duration"]),
        width=int(stream["width"]),
        height=int(stream["height"]),
        fps=_fps(stream["avg_frame_rate"]),
        has_audio=any(item["codec_type"] == "audio" for item in payload["streams"]),
    )


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
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(destination),
    ])
    probe_video(destination)
    return destination


def ensure_segment_clip(source: Path, destination: Path, start_sec: float, end_sec: float, max_sec: float) -> Path:
    """Create or reuse a persistent segment clip, reusing clip_video() without changing codec/audio flags."""
    if destination.is_file():
        metadata = probe_video(destination)
    else:
        metadata = probe_video(clip_video(source, destination, start_sec, end_sec))
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
