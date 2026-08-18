"""Pure full-video edit prompt document contract.

This module has no I/O or database dependency. It parses and validates the
official absolute-time full prompt, preserves the deterministic global prefix,
and derives segment-relative provider prompts deterministically.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Sequence

EPSILON = 0.001
_TIME_HEADER_RE = re.compile(r"^\d{2,}:\d{2}\.\d{2}–\d{2,}:\d{2}\.\d{2}$")
_COLUMN_ORDER = ("保持：", "修改：", "删除：", "禁止：")


class FullPromptValidationError(ValueError):
    """Raised when a full prompt violates the structural or coverage rules."""


@dataclass(frozen=True)
class PromptBlock:
    source_start_sec: float
    source_end_sec: float
    keep: str
    modify: str
    delete: str
    forbid: str


@dataclass(frozen=True)
class FullPromptDocument:
    global_prefix: str
    blocks: tuple[PromptBlock, ...]


def format_time_label(start_sec: float, end_sec: float) -> str:
    """Format an absolute source-time label; supports more than one minute digit.

    Uses floor to the centisecond so tiny floating-point noise (e.g. 4.1299999
    from arithmetic) always maps to the same label as the intended 4.12.
    """
    def stamp(value: float) -> str:
        total_centis = math.floor(value * 100 + 1e-9)
        minutes, centis = divmod(total_centis, 6000)
        seconds, centis = divmod(centis, 100)
        return f"{minutes:02d}:{seconds:02d}.{centis:02d}"
    return f"{stamp(start_sec)}–{stamp(end_sec)}"


def _parse_label(label: str) -> tuple[int, int]:
    """Parse an MM:SS.xx label into integer centiseconds (relative to zero)."""
    match = re.match(r"^(\d+):(\d{2})\.(\d{2})$", label)
    if not match:
        raise FullPromptValidationError(f"非法时间标签：{label}")
    minutes, seconds, centis = (int(part) for part in match.groups())
    return minutes * 6000 + seconds * 100 + centis


def expected_full_prompt_labels(shot_ranges: Sequence[tuple[float, float]]) -> list[str]:
    return [format_time_label(start, end) for start, end in shot_ranges]


def _split_time_header(line: str) -> tuple[str, str] | None:
    """Split an MM:SS.xx–MM:SS.xx header into (start_label, end_label) or None."""
    if not _TIME_HEADER_RE.match(line):
        return None
    start_label, end_label = line.split("–", 1)
    return start_label, end_label


def parse_full_prompt(text: str) -> FullPromptDocument:
    """Parse into a global prefix plus ordered absolute-time blocks.

    Normalizes CRLF, trims trailing whitespace per line. A time header is
    recognized only when an entire line matches the pattern.
    """
    normalized = text.replace("\r\n", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    # 去尾空行。
    while lines and not lines[-1].strip():
        lines.pop()
    global_prefix_lines: list[str] = []
    blocks: list[PromptBlock] = []
    index = 0
    # 找到第一个时间头之前的全部行作为全局前缀。
    first_header = None
    for i, line in enumerate(lines):
        if _split_time_header(line):
            first_header = i
            break
    if first_header is None:
        raise FullPromptValidationError("完整提示词缺少时间块")
    global_prefix_lines = lines[:first_header]
    index = first_header
    while index < len(lines):
        header = _split_time_header(lines[index])
        if header is None:
            raise FullPromptValidationError(f"时间块之间出现未归类内容：{lines[index]!r}")
        start_label, end_label = header
        start_sec = _parse_label(start_label) / 100.0
        end_sec = _parse_label(end_label) / 100.0
        if end_sec <= start_sec:
            raise FullPromptValidationError("时间块结束时间必须大于开始时间")
        index += 1
        # 读取四个栏目（固定顺序）。
        column_values: dict[str, str] = {}
        for column in _COLUMN_ORDER:
            if index >= len(lines) or not lines[index].startswith(column):
                raise FullPromptValidationError(f"时间块缺少栏目 {column}")
            body = lines[index][len(column):].strip()
            if not body:
                raise FullPromptValidationError(f"栏目 {column} 正文不能为空")
            column_values[column] = body
            index += 1
        # 允许时间块之间的空行；下一个时间头或文件尾结束本块。
        while index < len(lines) and not lines[index].strip():
            index += 1
        blocks.append(PromptBlock(
            source_start_sec=start_sec,
            source_end_sec=end_sec,
            keep=column_values["保持："],
            modify=column_values["修改："],
            delete=column_values["删除："],
            forbid=column_values["禁止："],
        ))
    prefix = "\n".join(global_prefix_lines).rstrip()
    # 全局标签唯一性：同一区间出现两次判为重复（无论位置）。
    seen_intervals = set()
    for block in blocks:
        interval = (round(block.source_start_sec, 3), round(block.source_end_sec, 3))
        if interval in seen_intervals:
            raise FullPromptValidationError("重复")
        seen_intervals.add(interval)
    # 逆序 / 重叠：块必须按起点严格递增且与前一块无重叠。
    for previous, current in zip(blocks, blocks[1:]):
        if current.source_start_sec < previous.source_end_sec - EPSILON:
            raise FullPromptValidationError("时间块顺序")
    return FullPromptDocument(global_prefix=prefix, blocks=tuple(blocks))


def validate_full_prompt(
    text: str,
    shot_ranges: Sequence[tuple[float, float]],
    *,
    required_prefixes: Sequence[str] = (),
) -> FullPromptDocument:
    document = parse_full_prompt(text)
    expected = expected_full_prompt_labels(shot_ranges)
    actual = [format_time_label(block.source_start_sec, block.source_end_sec) for block in document.blocks]
    # 标签必须按顺序一一对应，使用整数厘秒比较避免浮点误差。
    expected_cs = [(_parse_label(start), _parse_label(end)) for start, end in (label.split("–", 1) for label in expected)]
    actual_cs = [(_parse_label(start), _parse_label(end)) for start, end in (label.split("–", 1) for label in actual)]
    # parse 已做全局重复检测；此处数量不匹配直接报告多余/缺少。
    if len(actual_cs) != len(expected_cs):
        raise FullPromptValidationError("时间块多余或缺少")
    for (a_start, a_end), (e_start, e_end) in zip(actual_cs, expected_cs):
        if (a_start, a_end) != (e_start, e_end):
            raise FullPromptValidationError("时间块")
    # 前缀必须包含所有必需前缀片段。
    for prefix in required_prefixes:
        if prefix not in document.global_prefix:
            raise FullPromptValidationError(prefix)
    return document


def render_full_prompt(document: FullPromptDocument) -> str:
    parts = [document.global_prefix.rstrip(), ""]
    for block in document.blocks:
        parts.append(format_time_label(block.source_start_sec, block.source_end_sec))
        parts.append(f"保持：{block.keep}")
        parts.append(f"修改：{block.modify}")
        parts.append(f"删除：{block.delete}")
        parts.append(f"禁止：{block.forbid}")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def derive_segment_prompt(
    document: FullPromptDocument,
    *,
    segment_start_sec: float,
    segment_end_sec: float,
    batch_position: int,
    batch_size: int,
) -> str:
    """Deterministically derive a segment-relative provider prompt.

    Clips each intersecting block to the segment and converts to segment-relative
    time. The original deterministic prefix plus a server-owned batch/segment
    identification line are preserved. Bodies are copied byte-for-byte after
    newline normalization.
    """
    if segment_end_sec - segment_start_sec <= EPSILON:
        raise FullPromptValidationError("生成片段为空")
    if segment_start_sec < -EPSILON or segment_end_sec > document.blocks[-1].source_end_sec + EPSILON:
        raise FullPromptValidationError("生成片段超出提示词覆盖范围")
    parts: list[str] = []
    if document.global_prefix:
        parts.append(document.global_prefix)
    parts.append(f"片段 {batch_position}/{batch_size}：原视频 {format_time_label(segment_start_sec, segment_end_sec)} 的局部编辑指令")
    block_count = 0
    for block in document.blocks:
        overlap_start = max(block.source_start_sec, segment_start_sec)
        overlap_end = min(block.source_end_sec, segment_end_sec)
        if overlap_end - overlap_start <= EPSILON:
            continue
        relative_start = overlap_start - segment_start_sec
        relative_end = overlap_end - segment_start_sec
        block_count += 1
        parts.append(format_time_label(relative_start, relative_end))
        parts.append(f"保持：{block.keep}")
        parts.append(f"修改：{block.modify}")
        parts.append(f"删除：{block.delete}")
        parts.append(f"禁止：{block.forbid}")
        parts.append("")
    if block_count == 0:
        raise FullPromptValidationError("生成片段没有覆盖任何时间块")
    return "\n".join(parts).rstrip() + "\n"
