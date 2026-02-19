"""
简谱验证：负责解析入口，调用 parser 并收集错误与警告。
错误由 parser 抛出 ParseError，validator 负责捕获并格式化为 Diagnostic。
"""
import re
from dataclasses import dataclass

from parser import parse as _parse, ParseError, ParsedScore, _strip_comments, NoteEvent, ChordEvent


def _build_stripped_to_raw_mapping(raw_text: str) -> list[int]:
    """构建 stripped 位置到 raw 位置的映射。stripped 与 parser 去注释后一致。"""
    mapping: list[int] = []
    i = 0
    while i < len(raw_text):
        if i + 1 < len(raw_text) and raw_text[i : i + 2] == "//":
            while i < len(raw_text) and raw_text[i] != "\n":
                i += 1
            if i < len(raw_text):
                i += 1
            continue
        mapping.append(i)
        i += 1
    return mapping


@dataclass
class Diagnostic:
    """诊断信息"""
    line: int
    column: int
    message: str
    level: str  # "error" | "warning"
    start_pos: int | None = None
    end_pos: int | None = None


def _pos_to_line_col(text: str, pos: int) -> tuple[int, int]:
    """将字符位置转换为 (行号, 列号)，从 1 开始"""
    lines = text[:pos].split("\n")
    return len(lines), len(lines[-1]) + 1


def _check_bar_duration(text: str, score) -> list[Diagnostic]:
    """检查小节时值是否与拍号一致。支持篇章间不同拍号。按声部行匹配小节位置，避免多声部时错位。"""
    diags: list[Diagnostic] = []
    tol = 0.001

    def bar_duration(bar) -> float:
        total = 0.0
        for ev in bar.events:
            if hasattr(ev, "duration_beats"):
                total += ev.duration_beats
        return total

    # 构建 (bar, 该小节应有的拍数) 列表，按篇章、声部顺序
    bars_with_expected: list[tuple] = []
    sections = getattr(score, "sections", None) or ([score.parts] if score.parts else [])
    section_settings = getattr(score, "section_settings", None) or []
    default_beats = score.settings.beat_numerator / score.settings.beat_denominator
    if score.settings.no_bar_check:
        return diags

    for sec_idx, section in enumerate(sections):
        if sec_idx < len(section_settings):
            s = section_settings[sec_idx]
            if s.no_bar_check:
                expected = None
            else:
                expected = s.beat_numerator / s.beat_denominator
        else:
            expected = default_beats
        for part in section:
            for bar in part.bars:
                bars_with_expected.append((bar, expected))

    # 按 parser 相同的结构遍历：sections -> parts -> bars，与 bars_with_expected 顺序一致
    from parser import _parse_global, _split_sections

    _, rest = _parse_global(text)
    rest_start = text.find(rest) if rest in text else 0
    sections_raw = _split_sections(rest)

    bar_idx = 0
    block_offset = 0
    sec_idx = 0
    for block, part_lines in sections_raw:
        if not part_lines:
            block_in_rest = rest.find(block, block_offset)
            block_offset = (block_in_rest + len(block) + 2) if block_in_rest >= 0 else block_offset
            continue
        if sec_idx >= len(sections):
            break
        block_in_rest = rest.find(block, block_offset)
        if block_in_rest < 0:
            block_in_rest = block_offset
        block_abs_start = rest_start + block_in_rest
        block_offset = block_in_rest + len(block) + 2

        part_line_search = 0
        for part_idx, part_line in enumerate(part_lines):
            if part_idx >= len(sections[sec_idx]):
                break
            part_line_offset = block.find(part_line, part_line_search)
            if part_line_offset < 0:
                part_line_offset = part_line_search
            part_line_abs_start = block_abs_start + part_line_offset

            pipe_positions: list[int] = []
            for j, ch in enumerate(part_line):
                if ch == "|":
                    pipe_positions.append(part_line_abs_start + j)

            part_bars = sections[sec_idx][part_idx].bars
            for bar_idx_in_part in range(len(part_bars)):
                if bar_idx >= len(bars_with_expected):
                    break
                bar, beats_per_bar = bars_with_expected[bar_idx]
                if beats_per_bar is not None:
                    dur = bar_duration(bar)
                    tie_adj = getattr(bar, "tie_adjustment", 0.0)
                    effective_dur = dur + tie_adj
                    if abs(effective_dur - beats_per_bar) > tol:
                        # 小节范围：第 i 个小节从第 i 个 | 到第 i+1 个 |（或行尾）
                        i = bar_idx_in_part
                        bar_start = pipe_positions[i] if i < len(pipe_positions) else part_line_abs_start
                        bar_end = (
                            pipe_positions[i + 1]
                            if i + 1 < len(pipe_positions)
                            else part_line_abs_start + len(part_line)
                        )
                        if bar_start < len(text) and text[bar_start] != "|":
                            for off in range(1, 6):
                                if bar_start >= off and text[bar_start - off] == "|":
                                    bar_start -= off
                                    break
                        line, col = _pos_to_line_col(text, bar_start)
                        diags.append(Diagnostic(
                            line, col,
                            f"小节时值不一致：当前 {effective_dur:.2f} 拍，应为 {beats_per_bar:.1f} 拍",
                            "warning",
                            start_pos=bar_start,
                            end_pos=bar_end,
                        ))
                bar_idx += 1

            part_line_search = part_line_offset + len(part_line) + 1

        sec_idx += 1

    # 最后一小节（无结束 | 时）
    if bar_idx < len(bars_with_expected):
        bar, beats_per_bar = bars_with_expected[bar_idx]
        if beats_per_bar is not None:
            dur = bar_duration(bar)
            tie_adj = getattr(bar, "tie_adjustment", 0.0)
            effective_dur = dur + tie_adj
            if abs(effective_dur - beats_per_bar) > tol:
                diags.append(Diagnostic(
                    1, 1,
                    f"小节时值不一致：当前 {effective_dur:.2f} 拍，应为 {beats_per_bar:.1f} 拍",
                    "warning",
                    start_pos=0,
                    end_pos=min(1, len(text)),
                ))
    return diags


def _check_unrecognized(text: str) -> list[Diagnostic]:
    """检查无法识别的音符或记号"""
    diags: list[Diagnostic] = []
    lines = text.split("\n")
    for line_no, line in enumerate(lines, 1):
        if "//" in line:
            line = line[: line.index("//")].rstrip()
        stripped = line.strip()
        if stripped.startswith("\\"):
            continue
        content = stripped[1:].lstrip() if stripped.startswith("&") else line
        col_offset = len(line) - len(content) + 1
        line_start_pos = sum(len(ln) + 1 for ln in lines[: line_no - 1])
        depth = 0
        i = 0
        while i < len(content):
            c = content[i]
            if c in "[(":
                depth += 1
                i += 1
                continue
            if c in "])":
                depth -= 1
                i += 1
                continue
            if depth > 0:
                i += 1
                continue
            if content[i] in " \t\n|":
                i += 1
                continue
            j = i
            while j < len(content) and content[j] not in " \t\n|[]()":
                j += 1
            tok = content[i:j]
            if tok:
                start_p = line_start_pos + (col_offset - 1) + i
                end_p = start_p + len(tok)
                if re.match(r"^[#b^.]*9([.-_~/]|$)", tok):
                    diags.append(Diagnostic(line_no, col_offset + i + 1, f"无法识别的音符「{tok}」：简谱仅支持 1-7", "warning", start_p, end_p))
                elif re.match(r"^[#b^.]*\d", tok):
                    num = re.search(r"\d", tok)
                    if num:
                        d = int(tok[num.start()])
                        if d > 8:
                            diags.append(Diagnostic(line_no, col_offset + i + num.start() + 1, f"无法识别的音符「{tok}」：简谱仅支持 0-7，8 表示重复上小节", "warning", start_p, end_p))
            i = j
    return diags


VOICEVOX_UNREACHABLE_MSG = "VOICEVOX 引擎未连接：请确认 voicevox_engine 已启动（默认端口 50021）"


def _has_voicevox_usage(text: str) -> bool:
    """内容是否包含 \\lyrics 或 \\tts 的 voice_id（会用到 VOICEVOX）"""
    # \lyrics{syllables}{part}{voice_id} 需至少 {part}{voice_id}
    if re.search(r"\\lyrics\{[^{}]*\}\{\d+\}\{[^{}]+\}", text, re.I):
        return True
    # \tts{text}{lang}{voice_id} 需 {lang}{voice_id}
    if re.search(r"\\tts\{[^{}]*\}\{[^{}]+\}\{[^{}]+\}", text, re.I):
        return True
    return False


def _check_voicevox_connection(text: str) -> tuple[list[Diagnostic], bool]:
    """检查 VOICEVOX 连接。返回 (diags, connected)。当内容无 voicevox 用法时 connected=True。"""
    diags: list[Diagnostic] = []
    if not _has_voicevox_usage(text):
        return diags, True
    try:
        from voicevox_client import fetch_singers, VOICEVOX_BASE
    except ImportError:
        return diags, True
    try:
        fetch_singers(VOICEVOX_BASE)
        return diags, True
    except Exception:
        diags.append(Diagnostic(1, 1, VOICEVOX_UNREACHABLE_MSG, "warning", None, None))
        return diags, False


def _check_lyrics_singing_support(text: str, score: ParsedScore) -> list[Diagnostic]:
    """检查 \\lyrics 中的 voice_id 是否支持歌唱。连接不上引擎时静默跳过。"""
    diags: list[Diagnostic] = []
    try:
        from voicevox_client import fetch_singers, VOICEVOX_BASE
    except ImportError:
        return diags
    try:
        singers = fetch_singers(VOICEVOX_BASE)
    except Exception:
        return diags  # 连接失败时静默跳过（连接警告由 _check_voicevox_connection 处理）
    singing_ids = {st.get("id") for s in singers for st in s.get("styles", []) if st.get("id") is not None}
    if not singing_ids:
        return diags
    section_lyrics = getattr(score, "section_lyrics", None) or []
    unsupported: list[int] = []
    for sec in section_lyrics:
        for _part_idx, _syllables, voice_id, _melody in sec:
            if voice_id is not None and voice_id not in singing_ids and voice_id not in unsupported:
                unsupported.append(voice_id)
    if not unsupported:
        return diags
    m = re.search(r"\\lyrics\{[^{}]*\}(?:\{\d+\})?(?:\{[^{}]+})?(?:\{[01]})?\s*", text, re.I)
    if m:
        line, col = _pos_to_line_col(text, m.start())
        diags.append(Diagnostic(
            line, col,
            f"所选音色（style_id: {unsupported[0]}）不支持歌唱，将使用默认歌唱角色。请查阅VOICEVOX面板。",
            "warning",
            m.start(),
            m.end(),
        ))
    else:
        diags.append(Diagnostic(1, 1, "所选音色可能不支持歌唱，将使用默认歌唱角色。请查阅VOICEVOX面板。", "warning", 0, 0))
    return diags


def _check_instrument_range(score: ParsedScore, text: str) -> list[Diagnostic]:
    """检查各声部音符是否在指定乐器音域内。超出则报错。"""
    diags: list[Diagnostic] = []
    try:
        from instrument_registry import can_play_note, can_play_chord, midi_to_note_name
    except ImportError:
        return diags

    sections = getattr(score, "sections", None) or ([score.parts] if score.parts else [])
    section_part_instruments = getattr(score, "section_part_instruments", None) or []

    # 计算各篇章在原文中的起始位置（按 \n\n 分块）
    section_starts: list[int] = []
    pos = 0
    blocks = []
    depth = 0
    for i, c in enumerate(text + "\n\n"):
        if c == "\n" and i + 1 < len(text) + 2 and text[i : i + 2] == "\n\n" and depth == 0:
            chunk = text[pos:i].strip()
            if chunk:
                blocks.append((pos, chunk))
            pos = i + 2
            while pos < len(text) and text[pos] in "\n\t ":
                pos += 1
        elif c in "[(":
            depth += 1
        elif c in "])":
            depth -= 1
    if pos < len(text) and text[pos:].strip():
        blocks.append((pos, text[pos:].strip()))

    for sec_idx, section in enumerate(sections):
        part_instruments = (
            section_part_instruments[sec_idx]
            if sec_idx < len(section_part_instruments)
            else {}
        )
        section_pos = blocks[sec_idx][0] if sec_idx < len(blocks) else 0

        for part_idx, part in enumerate(section):
            instrument = part_instruments.get(part_idx, "grand_piano")

            for bar in part.bars:
                for ev in bar.events:
                    if isinstance(ev, NoteEvent):
                        if not can_play_note(instrument, ev.midi):
                            note_name = midi_to_note_name(ev.midi)
                            line, col = _pos_to_line_col(text, section_pos)
                            diags.append(Diagnostic(
                                line, col,
                                f"音符 {note_name}（MIDI {ev.midi}）超出 [{instrument}] 音域，无法弹奏",
                                "error",
                                section_pos,
                                section_pos + 1,
                            ))
                            return diags  # 先报第一个错误，避免刷屏
                    elif isinstance(ev, ChordEvent):
                        if not can_play_chord(instrument, ev.midis):
                            note_names = " ".join(midi_to_note_name(m) for m in ev.midis)
                            line, col = _pos_to_line_col(text, section_pos)
                            diags.append(Diagnostic(
                                line, col,
                                f"和弦 {note_names} 超出 [{instrument}] 音域，无法弹奏",
                                "error",
                                section_pos,
                                section_pos + 1,
                            ))
                            return diags

    return diags


def _check_fullwidth(text: str) -> list[Diagnostic]:
    """检查全角字符"""
    diags: list[Diagnostic] = []
    lines = text.split("\n")
    for line_no, line in enumerate(lines, 1):
        line_start = sum(len(ln) + 1 for ln in lines[: line_no - 1])
        for col, c in enumerate(line, 1):
            code = ord(c)
            if (0xFF01 <= code <= 0xFF5E) or (0xFF10 <= code <= 0xFF19) or code == 0x3000:
                pos = line_start + col - 1
                diags.append(Diagnostic(line_no, col, f"全角字符「{c}」", "warning", pos, pos + 1))
    return diags


def parse(text: str):
    """
    解析简谱，返回 ParsedScore。
    错误由 parser 抛出 ParseError。
    """
    return _parse(text)


def validate(text: str) -> tuple[ParsedScore | None, list[Diagnostic]]:
    """
    负责解析入口：调用 parser，捕获 ParseError，收集错误与警告。
    返回 (ParsedScore | None, list[Diagnostic])。
    若解析成功，返回 (score, diagnostics)；若解析失败，返回 (None, diagnostics)。
    """
    diags: list[Diagnostic] = []
    score = None
    try:
        score = _parse(text)
    except ParseError as e:
        diags.append(Diagnostic(
            e.line, e.column, e.message, "error",
            e.start_pos, e.end_pos,
        ))
        return None, diags
    except Exception as e:
        pos = 0
        lines = text.split("\n")
        if lines:
            pos = sum(len(ln) + 1 for ln in lines[:-1])
        diags.append(Diagnostic(1, 1, f"解析错误：{e}", "error", pos, min(pos + 1, len(text))))
        return None, diags

    if score:
        stripped = _strip_comments(text)
        mapping = _build_stripped_to_raw_mapping(text)
        bar_diags = _check_bar_duration(stripped, score)
        for d in bar_diags:
            if d.start_pos is not None and d.start_pos < len(mapping):
                d.start_pos = mapping[d.start_pos]
            if d.end_pos is not None:
                if 0 < d.end_pos <= len(mapping):
                    d.end_pos = mapping[d.end_pos - 1] + 1
                elif d.end_pos > len(mapping):
                    d.end_pos = len(text)
            if d.start_pos is not None:
                d.line, d.column = _pos_to_line_col(text, d.start_pos)
        diags.extend(bar_diags)
        conn_diags, connected = _check_voicevox_connection(text)
        diags.extend(conn_diags)
        if connected:
            diags.extend(_check_lyrics_singing_support(text, score))
        diags.extend(_check_instrument_range(score, stripped))
    diags.extend(_check_unrecognized(text))
    diags.extend(_check_fullwidth(text))
    diags.sort(key=lambda d: (d.line, d.column))
    return score, diags
