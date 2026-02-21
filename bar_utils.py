"""
小节高亮与预览：根据光标位置检测所在小节，支持多声部同时高亮。
"""
import re

from parser import _parse_global, _split_sections


def _pipe_positions_at_depth_zero(line: str) -> list[int]:
    """
    返回 line 中作为小节边界的 | 位置列表。
    仅按方括号 [ ] 计深度：记号作用域 [8vb](|bar1|bar2|) 内的 | 仍为小节线；
    圆括号 ( ) 用于和弦等，其内无 |，故不参与深度判断。
    """
    positions: list[int] = []
    depth_bracket = 0
    for i, c in enumerate(line):
        if c == "[":
            depth_bracket += 1
        elif c == "]":
            depth_bracket -= 1
        elif c == "|" and depth_bracket == 0:
            positions.append(i)
    return positions


def get_bar_ranges_at_cursor(content: str, cursor_pos: int) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """
    根据光标位置返回小节高亮范围。
    返回 (current_bar_ranges, simultaneous_bar_ranges)：
    - current_bar_ranges: 光标所在声部的当前小节范围 [(start, end), ...]（可能跨行）
    - simultaneous_bar_ranges: 多声部时，所有同时演奏的小节范围（更浅色高亮）
    """
    current: list[tuple[int, int]] = []
    simultaneous: list[tuple[int, int]] = []
    try:
        _, rest = _parse_global(content)
    except Exception:
        return current, simultaneous
    rest_start = content.find(rest) if rest and rest in content else 0
    sections = _split_sections(rest)
    if not sections:
        return current, simultaneous

    # 找到包含 cursor_pos 的 block 和 part_line
    abs_pos = 0
    found_part_idx = -1
    found_bar_idx = -1
    part_lines_of_section: list[str] = []
    block_abs_start = 0
    part_line_abs_starts: list[int] = []

    for block, part_lines in sections:
        if not part_lines:
            continue
        block_in_rest = rest.find(block, 0)
        if block_in_rest < 0:
            block_in_rest = 0
        block_abs_start = rest_start + block_in_rest
        part_line_search = 0
        for part_idx, part_line in enumerate(part_lines):
            part_line_offset = block.find(part_line, part_line_search)
            if part_line_offset < 0:
                part_line_offset = part_line_search
            part_line_abs_start = block_abs_start + part_line_offset
            part_line_abs_end = part_line_abs_start + len(part_line)
            if part_line_abs_start <= cursor_pos < part_line_abs_end:
                pipes = _pipe_positions_at_depth_zero(part_line)
                if not pipes:
                    break
                bar_idx = -1
                for bi in range(len(pipes) - 1):
                    bar_start = part_line_abs_start + pipes[bi]
                    bar_end = part_line_abs_start + pipes[bi + 1]
                    if bar_start <= cursor_pos < bar_end:
                        bar_idx = bi
                        break
                if bar_idx < 0 and pipes:
                    if cursor_pos < part_line_abs_start + pipes[0]:
                        bar_idx = 0
                    else:
                        bar_idx = len(pipes) - 1
                if bar_idx >= 0:
                    found_part_idx = part_idx
                    found_bar_idx = bar_idx
                    part_lines_of_section = part_lines
                    part_line_abs_starts = []
                    ps = 0
                    for pl in part_lines:
                        po = block.find(pl, ps)
                        if po < 0:
                            po = ps
                        part_line_abs_starts.append(block_abs_start + po)
                        ps = po + len(pl) if po >= 0 else ps
                    break
            part_line_search = part_line_offset + len(part_line)
        if found_part_idx >= 0:
            break

    if found_part_idx < 0 or found_bar_idx < 0:
        return current, simultaneous

    for part_idx, part_line in enumerate(part_lines_of_section):
        pipes = _pipe_positions_at_depth_zero(part_line)
        if found_bar_idx >= len(pipes) - 1 and pipes:
            bar_start = part_line_abs_starts[part_idx] + pipes[-1]
            bar_end = part_line_abs_starts[part_idx] + len(part_line)
        elif found_bar_idx < len(pipes) - 1:
            bar_start = part_line_abs_starts[part_idx] + pipes[found_bar_idx]
            bar_end = part_line_abs_starts[part_idx] + pipes[found_bar_idx + 1]
        else:
            continue
        r = (bar_start, bar_end)
        if part_idx == found_part_idx:
            current.append(r)
        simultaneous.append(r)

    return current, simultaneous


def _extract_defines_for_preview(content: str) -> str:
    """从 content 中提取所有 \\define{key}{value}，返回可拼接到预览前的字符串。"""
    define_pattern = re.compile(r"\\define\{([^{}]+)\}\{([^{}]*)\}\s*", re.I)
    parts: list[str] = []
    for m in define_pattern.finditer(content):
        key, value = m.group(1).strip(), m.group(2)
        if key:
            parts.append(f"\\define{{{key}}}{{{value}}}\n")
    return "".join(parts)


def extract_single_bar_for_preview(content: str, bar_start: int, bar_end: int) -> str | None:
    """
    从 content 中提取单小节内容，构建可播放的最小简谱。
    包含：\\define、全局设定、该声部该小节的音色前缀（如 [8vb]([drums][ppp]|）及小节内容。
    """
    bar_text = content[bar_start:bar_end].strip()
    if not bar_text or bar_text == "|":
        return None
    bar_text = bar_text.strip("|").strip()
    if not bar_text:
        return None
    try:
        settings, _ = _parse_global(content)
        tonality = getattr(settings, "tonality", "0")
        beat_num = getattr(settings, "beat_numerator", 4)
        beat_den = getattr(settings, "beat_denominator", 4)
        bpm = getattr(settings, "bpm", 120)
    except Exception:
        tonality, beat_num, beat_den, bpm = "0", 4, 4, 120

    # 音色前缀：从行首到该行第一个小节线 |（含 [8vb]([drums][ppp]|），不含前面小节内容
    line_start = content.rfind("\n", 0, bar_start) + 1
    line_content = content[line_start:]
    if "\n" in line_content:
        line_content = line_content[: line_content.index("\n")]
    pipes = _pipe_positions_at_depth_zero(line_content)
    if pipes and line_content.strip().startswith("&"):
        # 前缀到第一个 |（含），即 & [8vb]([drums][ppp]| 或 & [ff]|
        prefix_len = pipes[0] + 1
        voice_prefix = content[line_start : line_start + prefix_len]
        # bar_content 含前导 |，需去掉以免重复；只取两 pipe 之间的内容
        bar_only = content[bar_start + 1 : bar_end].rstrip()
        # 仅当前缀含 ( 时才用 |) 闭合，否则用 |（如 & [ff]| bar | 无括号）
        close = "|)" if "(" in voice_prefix else "|"
        voice_line = voice_prefix + bar_only + close
    else:
        voice_line = f"| {bar_text} |"

    defines_block = _extract_defines_for_preview(content)
    return (
        f"{defines_block}\\tonality{{{tonality}}}\n\\beat{{{beat_num}/{beat_den}}}\n\\bpm{{{bpm}}}\n"
        f"{voice_line}"
    )
