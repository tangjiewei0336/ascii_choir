"""
简谱验证：负责解析入口，调用 parser 并收集错误与警告。
错误由 parser 抛出 ParseError，validator 负责捕获并格式化为 Diagnostic。
"""
import re
from dataclasses import dataclass

from parser import parse as _parse, ParseError, ParsedScore, _strip_comments


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
    """检查小节时值是否与拍号一致。支持篇章间不同拍号。"""
    diags: list[Diagnostic] = []
    tol = 0.001

    def bar_duration(bar) -> float:
        total = 0.0
        for ev in bar.events:
            if hasattr(ev, "duration_beats"):
                total += ev.duration_beats
        return total

    # 构建 (bar, 该小节应有的拍数) 列表，按篇章使用对应拍号
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
                expected = None  # 不检查该篇章小节时值
            else:
                expected = s.beat_numerator / s.beat_denominator
        else:
            expected = default_beats
        for part in section:
            for bar in part.bars:
                bars_with_expected.append((bar, expected))

    bar_start = None
    bar_idx = 0
    i = 0
    while i < len(text):
        if i < len(text) - 1 and text[i : i + 2] == "//":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        c = text[i]
        if c == "|":
            if bar_start is not None:
                if bar_idx < len(bars_with_expected):
                    bar, beats_per_bar = bars_with_expected[bar_idx]
                    if beats_per_bar is not None:
                        dur = bar_duration(bar)
                        tie_adj = getattr(bar, "tie_adjustment", 0.0)
                        effective_dur = dur + tie_adj
                        if abs(effective_dur - beats_per_bar) > tol:
                            line, col = _pos_to_line_col(text, bar_start)
                            diags.append(Diagnostic(
                                line, col,
                                f"小节时值不一致：当前 {effective_dur:.2f} 拍，应为 {beats_per_bar:.1f} 拍",
                                "warning",
                                start_pos=bar_start,
                                end_pos=i,
                            ))
                bar_idx += 1
            bar_start = i
        i += 1
    if bar_start is not None and bar_idx < len(bars_with_expected):
        bar, beats_per_bar = bars_with_expected[bar_idx]
        if beats_per_bar is not None:
            dur = bar_duration(bar)
            tie_adj = getattr(bar, "tie_adjustment", 0.0)
            effective_dur = dur + tie_adj
            if abs(effective_dur - beats_per_bar) > tol:
                line, col = _pos_to_line_col(text, bar_start)
                diags.append(Diagnostic(
                    line, col,
                    f"小节时值不一致：当前 {effective_dur:.2f} 拍，应为 {beats_per_bar:.1f} 拍",
                    "warning",
                    start_pos=bar_start,
                    end_pos=len(text),
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
    diags.extend(_check_unrecognized(text))
    diags.extend(_check_fullwidth(text))
    diags.sort(key=lambda d: (d.line, d.column))
    return score, diags
