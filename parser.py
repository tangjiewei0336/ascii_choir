"""
简谱解析器：解析全局设定和简谱语法，支持括号跨小节。
错误由解析过程抛出 ParseError。
"""
import re
from dataclasses import dataclass, field
from typing import Optional


class ParseError(Exception):
    """解析错误，包含位置信息"""
    def __init__(self, line: int, column: int, message: str, start_pos: int | None = None, end_pos: int | None = None):
        self.line = line
        self.column = column
        self.message = message
        self.start_pos = start_pos
        self.end_pos = end_pos
        super().__init__(f"第{line}行第{column}列: {message}")


# C 大调基准 (tonality 1)
C_MAJOR_BASE = {1: 60, 2: 62, 3: 64, 4: 65, 5: 67, 6: 69, 7: 71}

# 和声用：音名（按音程）往下/上数。+3 往上三度（2个音名），-3 往下三度；+5/-5 同理。
# 自然音 pitch class -> 简谱级数 1-7
_PC_TO_DEGREE = {0: 1, 2: 2, 4: 3, 5: 4, 7: 5, 9: 6, 11: 7}
_DEGREE_TO_PC = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 11}

# 调性偏移：bA=Ab, bB=Bb 等，简化为降号调；b1–b7 为数字形式的降号调
TONALITY_FLAT_OFFSET = {"A": -1, "B": -2, "C": 0, "D": -1, "E": -2, "F": -1, "G": -2}
# 1–7 对应 CDEFGAB，b+数字 的降号调半音偏移
TONALITY_FLAT_BY_DEGREE = {1: -1, 2: -1, 3: -2, 4: -1, 5: -2, 6: -1, 7: -2}
# #+数字 的升号调半音偏移
TONALITY_SHARP_BY_DEGREE = {1: 1, 2: 2, 3: 5, 4: 6, 5: 8, 6: 10, 7: 0}
# 1–7 无前缀：C/D/E/F/G/A/B 大调半音偏移
TONALITY_MAJOR_OFFSET = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 11}
# 字母 C–B 大调；#C–#B 升号调
TONALITY_LETTER_OFFSET = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
TONALITY_SHARP_LETTER = {"C": 1, "D": 3, "E": 5, "F": 6, "G": 8, "A": 10, "B": 0}

# 音量映射
VOLUME_MAP = {
    "fff": 1.0, "ff": 0.9, "f": 0.8, "mf": 0.7, "mp": 0.5, "p": 0.4, "pp": 0.3, "ppp": 0.2,
}


@dataclass
class GlobalSettings:
    tonality: str = "1"
    beat_numerator: int = 4
    beat_denominator: int = 4
    bpm: int = 120
    no_bar_check: bool = False


@dataclass
class NoteEvent:
    midi: int
    duration_beats: float
    volume: float = 0.6


@dataclass
class ChordEvent:
    midis: list[int]
    duration_beats: float
    volume: float = 0.6


@dataclass
class RestEvent:
    duration_beats: float


@dataclass
class BarContent:
    events: list = field(default_factory=list)
    dc: bool = False   # [dc] Da Capo：从此处跳回开头
    fine: bool = False  # [fine]：在此处结束


@dataclass
class Part:
    bars: list[BarContent] = field(default_factory=list)


@dataclass
class ParsedScore:
    settings: GlobalSettings
    parts: list[Part] = field(default_factory=list)  # 兼容：扁平化所有篇章
    sections: list[list[Part]] = field(default_factory=list)  # 篇章列表，每篇章 & 数量相同
    section_settings: list[GlobalSettings] = field(default_factory=list)  # 每篇章的独立设定（覆盖前面的）


def _copy_settings(s: GlobalSettings) -> GlobalSettings:
    """复制全局设定"""
    return GlobalSettings(
        tonality=s.tonality,
        beat_numerator=s.beat_numerator,
        beat_denominator=s.beat_denominator,
        bpm=s.bpm,
        no_bar_check=s.no_bar_check,
    )


def _parse_global(text: str, inherit: GlobalSettings | None = None) -> tuple[GlobalSettings, str]:
    """解析全局设定。inherit 为 None 时从默认值开始；否则继承并覆盖"""
    settings = _copy_settings(inherit) if inherit else GlobalSettings()
    rest = text.strip()
    while rest:
        m = re.match(r"\\tonality\{([^}]+)\}\s*", rest, re.I)
        if m:
            settings.tonality = m.group(1).strip()
            rest = rest[m.end():]
            continue
        m = re.match(r"\\beat\{([^}]+)\}\s*", rest, re.I)
        if m:
            val = m.group(1).strip().lower()
            if val == "c":
                settings.beat_numerator, settings.beat_denominator = 4, 4
            elif val == "cut":
                settings.beat_numerator, settings.beat_denominator = 2, 2
            else:
                parts = val.split("/")
                if len(parts) == 2:
                    try:
                        settings.beat_numerator = int(parts[0])
                        settings.beat_denominator = int(parts[1])
                    except ValueError:
                        pass
            rest = rest[m.end():]
            continue
        m = re.match(r"\\bpm\{(\d+)\}\s*", rest, re.I)
        if m:
            try:
                settings.bpm = int(m.group(1))
            except ValueError:
                pass
            rest = rest[m.end():]
            continue
        m = re.match(r"\\no_bar_check\s*", rest, re.I)
        if m:
            settings.no_bar_check = True
            rest = rest[m.end():]
            continue
        break
    return settings, rest


def _tonality_to_semitones(tonality: str) -> int:
    """调性转半音偏移。1=C, b1=Cb, bA=Ab, #1=C#, 或直接整数如 +2/-1 表示统一偏移"""
    t = tonality.strip()
    # 1–7 无前缀：C/D/E/F/G/A/B 大调
    if t.isdigit() and 1 <= int(t) <= 7:
        return TONALITY_MAJOR_OFFSET[int(t)]
    # 直接整数：+2, -1, 8 等表示统一半音偏移（带符号或 >7 的数字）
    m_int = re.match(r"([+-]?\d+)\s*$", t)
    if m_int:
        return int(m_int.group(1))
    # b + 数字：b1=Cb, b2=Db 等
    m_bnum = re.match(r"b(\d)\s*$", t, re.I)
    if m_bnum:
        d = int(m_bnum.group(1))
        return TONALITY_FLAT_BY_DEGREE.get(d, 0)
    # b + 字母：bA=Ab, bB=Bb 等
    m = re.match(r"b([A-Ga-g])\s*$", t, re.I)
    if m:
        note = m.group(1).upper()
        return TONALITY_FLAT_OFFSET.get(note, 0)
    # # + 数字：#1=C#, #2=D# 等
    m_sharp = re.match(r"#(\d)\s*$", t)
    if m_sharp:
        d = int(m_sharp.group(1))
        return TONALITY_SHARP_BY_DEGREE.get(d, 0)
    # # + 字母：#C=C#, #D=D# 等
    m_sharp_let = re.match(r"#([A-Ga-g])\s*$", t)
    if m_sharp_let:
        return TONALITY_SHARP_LETTER.get(m_sharp_let.group(1).upper(), 0)
    # 纯字母：C, D, E, F, G, A, B
    if len(t) == 1 and t.upper() in "CDEFGAB":
        return TONALITY_LETTER_OFFSET.get(t.upper(), 0)
    return 0


def _simplified_to_midi(
    simplified: int,
    octave_offset: int,
    accidental: int,
    tonality_offset: int,
) -> int:
    """简谱数字转 MIDI。accidental: 0=无, 1=#, -1=b, 2=^"""
    if simplified < 1 or simplified > 7:
        return 60
    base = C_MAJOR_BASE[simplified]
    if accidental == 2:  # 还原
        pass
    elif accidental == 1:
        base += 1
    elif accidental == -1:
        base -= 1
    return base + octave_offset * 12 + tonality_offset


def _has_accidental(tok: str) -> bool:
    """检查 token 是否含升降号 # b ^"""
    t = tok.strip()
    return bool(t) and (t.startswith("#") or t.startswith("^") or (t.startswith("b") and len(t) > 1 and t[1].isdigit()))


def _any_accidental_in_tokens(tokens: list[str]) -> Optional[str]:
    """若任一 token（含 / 拆分后的部分）含升降号，返回该 token，否则 None"""
    for t in tokens:
        if not t or t in "-_":
            continue
        if "/" in t:
            for p in t.split("/"):
                if _has_accidental(p):
                    return p
        elif _has_accidental(t):
            return t
    return None


def _pos_to_line_col(text: str, pos: int) -> tuple[int, int]:
    """将字符位置转换为 (行号, 列号)，从 1 开始"""
    lines = text[:pos].split("\n")
    return len(lines), len(lines[-1]) + 1


def _check_brackets_raise(text: str) -> None:
    """检查 [ ] ( ) 是否配对，否则抛出 ParseError"""
    stack_sq: list[int] = []
    stack_paren: list[int] = []
    for i, c in enumerate(text):
        if c == "[":
            stack_sq.append(i)
        elif c == "]":
            if stack_sq:
                stack_sq.pop()
            else:
                line, col = _pos_to_line_col(text, i)
                raise ParseError(line, col, "多余的 ]，无匹配的 [", i, i + 1)
        elif c == "(":
            stack_paren.append(i)
        elif c == ")":
            if stack_paren:
                stack_paren.pop()
            else:
                line, col = _pos_to_line_col(text, i)
                raise ParseError(line, col, "多余的 )，无匹配的 (", i, i + 1)
    for pos in stack_sq:
        line, col = _pos_to_line_col(text, pos)
        raise ParseError(line, col, "[ 未闭合", pos, pos + 1)
    for pos in stack_paren:
        line, col = _pos_to_line_col(text, pos)
        raise ParseError(line, col, "( 未闭合", pos, pos + 1)


def _find_matching_paren(s: str, start: int) -> int:
    """从 start 找 ( 对应的 )"""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _find_matching_bracket(s: str, start: int) -> int:
    """从 start 找 [ 对应的 ]"""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "[":
            depth += 1
        elif s[i] == "]":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _extract_bracket_content(line: str) -> tuple[Optional[str], Optional[str], int, str, bool, bool]:
    """
    提取 [xxx](content) 或 [xxx]。返回 (notation, content, end_pos, rest, apply_underscore, rest_starts_with_double_newline)。
    _ 优先级高于 []：若 ) 后紧跟 _，则对整块内容应用缩短。
    rest_starts_with_double_newline 表示 ) 后是否紧跟 \\n\\n，用于篇章边界检测。
    """
    line = line.lstrip()
    if not line.startswith("["):
        return None, None, 0, line, False, False
    end_b = _find_matching_bracket(line, 0)
    if end_b < 0:
        return None, None, 0, line, False, False
    inner = line[1:end_b].strip()
    rest = line[end_b + 1 :].lstrip()
    if rest.startswith("("):
        end_p = _find_matching_paren(rest, 0)
        if end_p >= 0:
            content = rest[1:end_p]
            rest_raw = rest[end_p + 1 :]  # 未 lstrip，用于检测 \n\n
            rest_after = rest_raw.lstrip()
            rest_starts_with_double_newline = rest_raw.startswith("\n\n")
            apply_underscore = rest_after.startswith("_")
            if apply_underscore:
                rest_after = rest_after[1:].lstrip()
            return inner, content, end_b + 1 + end_p + 1, rest_after, apply_underscore, rest_starts_with_double_newline
    return inner, None, end_b + 1, rest, False, False


def _halve_bar_events(bars: list[BarContent]) -> None:
    """将 bars 中所有事件的 duration_beats 减半"""
    for bar in bars:
        for ev in bar.events:
            if hasattr(ev, "duration_beats"):
                ev.duration_beats /= 2


def _parse_notation_scope(
    content: str,
    base_duration: float,
    beats_per_bar: float,
    part_octave: int,
    default_volume: float,
    deviation_explicit: bool,
    harmony: int,  # 0, +3, -3, +5, -5
    tonality_offset: int,
) -> tuple[list[BarContent], list[int]]:
    """
    解析一段内容（可含 | 跨小节），返回 (BarContent 列表, 篇章边界 bar 索引)。
    遇到 \\n\\n 时记录篇章边界，便于 |8| 跨篇章重复。
    """
    bars: list[BarContent] = []
    section_starts: list[int] = [0]
    current_bar = BarContent()
    bar_beats = 0.0
    i = 0
    content = content.strip()
    n = len(content)
    prev_note_midi: Optional[int] = None  # 用于 ~ 连音线
    volume = default_volume

    def flush_bar():
        nonlocal current_bar, bar_beats
        if current_bar.events or bar_beats > 0 or current_bar.dc or current_bar.fine:
            bars.append(current_bar)
        current_bar = BarContent()
        bar_beats = 0.0

    def parse_note_token(tok: str, oct_off: int, acc: int) -> Optional[NoteEvent | RestEvent]:
        tok = tok.strip()
        if not tok:
            return None
        if tok == "0" or tok.startswith("0"):
            dur = base_duration
            tilde_rest = False
            for c in tok[1:]:
                if c == "-":
                    dur += base_duration  # 增加一拍
                elif c == "_":
                    dur /= 2
                elif c == "~":
                    tilde_rest = True
            if tilde_rest:
                dur = max(0, beats_per_bar - bar_beats)
            return RestEvent(duration_beats=dur)
        # 剥离升降号前缀
        core = tok
        if core.startswith("#"):
            acc = 1
            core = core[1:]
        elif core.startswith("b") and len(core) > 1 and core[1].isdigit():
            acc = -1
            core = core[1:]
        elif core.startswith("^"):
            acc = 2
            core = core[1:]
        left_dots = right_dots = 0
        while core.startswith("."):
            left_dots += 1
            core = core[1:]
        # 先剥离末尾的 - 和 _，再识别右侧八度点（支持 1.----- 与 1-----.）
        ext = sum(1 for c in core if c == "-")
        shrt = sum(1 for c in core if c == "_")
        core = re.sub(r"[-_]+$", "", core)
        while core.endswith("."):
            right_dots += 1
            core = core[:-1]
        if not core or not core[0].isdigit():
            return None
        num = int(core[0])
        if num == 0:
            return RestEvent(duration_beats=base_duration * (1 + ext) * (0.5**shrt))
        dur = base_duration * (1 + ext) * (0.5**shrt)  # - 增加一拍，非乘2
        oct_final = part_octave - left_dots + right_dots
        midi = _simplified_to_midi(num, oct_final, acc, tonality_offset)
        return NoteEvent(midi=midi, duration_beats=dur, volume=volume)

    def parse_accidental(tok: str) -> int:
        if tok.startswith("#"):
            return 1
        if tok.startswith("b") and len(tok) > 1 and tok[1].isdigit():
            return -1
        if tok.startswith("^"):
            return 2
        return 0

    def add_harmony(midis: list[int], h: int, err_pos: int = 0) -> list[int]:
        """和声：按音名往下/上数。+3 往上三度，-3 往下三度；+5/-5 同理。含升降号的音不可用。"""
        if h == 0:
            return midis
        result = list(midis)
        for m in midis:
            pc = (m - tonality_offset) % 12
            if pc not in _PC_TO_DEGREE:
                line, col = _pos_to_line_col(content, err_pos) if err_pos >= 0 else (1, 1)
                raise ParseError(line, col, "和声记号 [+3][-3][+5][-5] 不可用于含升降号的音符", err_pos, err_pos + 1)
            degree = _PC_TO_DEGREE[pc]
            octave = (m - tonality_offset) // 12
            if h in (3, -3):
                delta = 2 if h > 0 else -2
            elif h in (5, -5):
                delta = 4 if h > 0 else -4
            else:
                continue
            new_deg = (degree - 1 + delta) % 7 + 1
            oct_delta = (degree - 1 + delta) // 7
            new_pc = _DEGREE_TO_PC[new_deg]
            new_midi = (octave + oct_delta) * 12 + tonality_offset + new_pc
            result.append(new_midi)
        return result

    depth = 0
    while i < n:
        # 跳过空白；遇 \n\n 且 depth==0 时记录篇章边界
        if content[i] in " \t\n":
            if i < n - 1 and content[i : i + 2] == "\n\n" and depth == 0:
                section_starts.append(len(bars))  # 下一小节起为新篇章
                i += 2
                while i < n and content[i] in " \t\n":
                    i += 1
                continue
            i += 1
            continue
        # 小节线
        if content[i] == "|":
            flush_bar()
            i += 1
            continue
        if content[i] == "]":
            depth -= 1
            i += 1
            continue
        if content[i] == ")":
            depth -= 1
            i += 1
            continue
        # [xxx] 或 [xxx](...)
        if content[i] == "[":
            depth += 1
            notation, scope, _, rest_after, apply_underscore, rest_starts_with_double_newline = _extract_bracket_content(content[i:])
            consumed = len(content[i:]) - len(rest_after)
            i += consumed
            depth -= 1
            if notation:
                notation_lower = notation.lower()
                # 音量
                if notation_lower in VOLUME_MAP:
                    volume = VOLUME_MAP[notation_lower]
                elif notation_lower in ("crescendo", "c"):
                    volume = min(1.0, volume + 0.1)  # 简化
                elif notation_lower in ("decrescendo", "d"):
                    volume = max(0.2, volume - 0.1)
                elif notation_lower in ("deviation explicit on",):
                    deviation_explicit = True
                elif notation_lower in ("deviation explicit off",):
                    deviation_explicit = False
                scope_octave = part_octave
                if notation_lower in ("8vb", "8va", "15va", "15vb"):
                    oct_off = -1 if "vb" in notation_lower else (1 if "8" in notation_lower else 2)
                    if "15" in notation_lower and "vb" in notation_lower:
                        oct_off = -2
                    scope_octave = part_octave + oct_off
                    if scope is None:
                        part_octave = scope_octave
                elif re.match(r"^[+-]?[35]$", notation_lower.replace(" ", "")):
                    h = notation_lower.replace(" ", "")
                    if h.startswith("+"):
                        harmony = int(h[1:])
                    else:
                        harmony = -int(h.lstrip("-"))
                elif notation_lower in ("dc",):
                    current_bar.dc = True
                elif notation_lower in ("fine",):
                    current_bar.fine = True
                if scope is not None:
                    sub_bars, _ = _parse_notation_scope(
                        scope, base_duration, beats_per_bar,
                        scope_octave, volume, deviation_explicit, harmony, tonality_offset,
                    )
                    if rest_starts_with_double_newline:
                        section_starts.append(len(bars) + len(sub_bars))
                    if apply_underscore:
                        _halve_bar_events(sub_bars)
                    for sb in sub_bars:
                        bars.append(sb)
                    current_bar = BarContent()
                    bar_beats = 0.0
            continue
        # (notes)n n连音 或 (notes)_ 括号应用到 _（_ 优先级高于 []）
        if content[i] == "(":
            depth += 1
            end_p = _find_matching_paren(content, i)
            if end_p < 0:
                i += 1
                continue
            inner = content[i + 1 : end_p]
            after = content[end_p + 1 :].lstrip()
            n_val = 3  # 默认三连音（有显式 n 时用）
            apply_underscore = False
            has_explicit_n = False
            if after and after[0] == "_":
                # (notes)_：均分后整体缩短一半
                apply_underscore = True
                i = end_p + 1 + 1  # consume )_
            elif after and after[0].isdigit():
                j = 0
                while j < len(after) and after[j].isdigit():
                    j += 1
                n_val = int(after[:j])
                has_explicit_n = True
                i = end_p + 1 + j
            else:
                i = end_p + 1
            depth -= 1
            tokens = re.split(r"\s+", inner.strip())
            acc_tok = _any_accidental_in_tokens(tokens)
            if harmony != 0 and acc_tok:
                pos = content.find(acc_tok, i + 1)
                if pos < 0:
                    pos = i
                line, col = _pos_to_line_col(content, pos)
                raise ParseError(line, col, "和声记号 [+3][-3][+5][-5] 不可用于含升降号的音符", pos, pos + len(acc_tok))
            notes_in_tuplet = []
            for t in tokens:
                if not t or t in "-_":
                    continue
                if "/" in t:
                    parts = t.split("/")
                    chord = []
                    for p in parts:
                        ev = parse_note_token(p, part_octave, 0)
                        if ev and isinstance(ev, NoteEvent):
                            chord.append(ev.midi)
                    if chord:
                        notes_in_tuplet.append(("chord", chord, base_duration))
                else:
                    ev = parse_note_token(t, part_octave, 0)
                    if ev and isinstance(ev, NoteEvent):
                        notes_in_tuplet.append(("note", [ev.midi], ev.duration_beats))
            if notes_in_tuplet:
                # 均分为 default；(notes)_：n=音符数并缩短一半；(notes)n：显式 n
                if not has_explicit_n:
                    n_val = len(notes_in_tuplet)
                each = base_duration / n_val
                if apply_underscore:
                    each /= 2
                for kind, midis, _ in notes_in_tuplet:
                    midis = add_harmony(midis, harmony, i)
                    if kind == "chord":
                        current_bar.events.append(ChordEvent(midis=midis, duration_beats=each, volume=volume))
                    else:
                        current_bar.events.append(NoteEvent(midi=midis[0], duration_beats=each, volume=volume))
                    bar_beats += each
            continue
        # 8 重复上小节
        if content[i] == "8" and (i + 1 >= n or content[i + 1] in " \t\n|"):
            if bars:
                current_bar = BarContent(events=[e for e in bars[-1].events])
                bar_beats = sum(
                    e.duration_beats for e in current_bar.events
                    if isinstance(e, (NoteEvent, ChordEvent, RestEvent))
                )
            i += 1
            continue
        # 收集 token
        j = i
        while j < n and content[j] not in " \t\n|[]()":
            j += 1
        tok = content[i:j]
        i = j

        if not tok:
            continue
        if tok == "-":
            if current_bar.events:
                last = current_bar.events[-1]
                if hasattr(last, "duration_beats"):
                    last.duration_beats += base_duration  # 增加一拍
                    bar_beats += base_duration
            continue
        if tok == "_":
            if current_bar.events:
                last = current_bar.events[-1]
                if hasattr(last, "duration_beats"):
                    last.duration_beats /= 2
                    bar_beats -= getattr(last, "duration_beats", 0)
            continue
        # ~ 连音线：与前音合并（可连到上一个小节）
        if tok.startswith("~"):
            tok = tok[1:].lstrip(".")
            # 优先当前小节，否则取上一小节最后一音
            last_ev = current_bar.events[-1] if current_bar.events else None
            last_in_current = last_ev is not None
            if last_ev is None and bars:
                prev_bar = bars[-1]
                if prev_bar.events:
                    last_ev = prev_bar.events[-1]
            if "/" in tok:
                parts = tok.split("/")
                chord = []
                for p in parts:
                    ev = parse_note_token(p, part_octave, 0)
                    if ev and isinstance(ev, NoteEvent):
                        chord.append(ev.midi)
                if chord and last_ev is not None:
                    if isinstance(last_ev, NoteEvent):
                        last_ev.duration_beats += base_duration
                    elif isinstance(last_ev, ChordEvent):
                        last_ev.duration_beats += base_duration
                    if last_in_current:
                        bar_beats += base_duration
            else:
                ev = parse_note_token(tok, part_octave, 0)
                if ev and isinstance(ev, NoteEvent) and last_ev is not None:
                    add_dur = ev.duration_beats
                    if isinstance(last_ev, NoteEvent) and last_ev.midi == ev.midi:
                        last_ev.duration_beats += add_dur
                    elif isinstance(last_ev, ChordEvent) and ev.midi in last_ev.midis:
                        last_ev.duration_beats += add_dur
                    if last_in_current:
                        bar_beats += add_dur
            continue
        # 和弦 1/3/5（每个音可单独带升降号）
        if "/" in tok:
            parts = tok.split("/")
            acc_tok = _any_accidental_in_tokens(parts) if harmony != 0 else None
            if acc_tok:
                pos = content.rfind(acc_tok, 0, i + len(tok) + 1)
                if pos < 0:
                    pos = i
                line, col = _pos_to_line_col(content, pos)
                raise ParseError(line, col, "和声记号 [+3][-3][+5][-5] 不可用于含升降号的音符", pos, pos + len(acc_tok))
            chord_midis = []
            max_dur = base_duration
            for p in parts:
                ev = parse_note_token(p, part_octave, 0)
                if ev and isinstance(ev, NoteEvent):
                    chord_midis.append(ev.midi)
                    max_dur = max(max_dur, ev.duration_beats)
            chord_midis = add_harmony(chord_midis, harmony, i)
            if chord_midis:
                current_bar.events.append(ChordEvent(midis=chord_midis, duration_beats=max_dur, volume=volume))
                bar_beats += max_dur
            continue
        # 单音
        if harmony != 0 and _has_accidental(tok):
            line, col = _pos_to_line_col(content, i)
            raise ParseError(line, col, "和声记号 [+3][-3][+5][-5] 不可用于含升降号的音符", i, i + len(tok))
        acc = parse_accidental(tok)
        ev = parse_note_token(tok, part_octave, acc)
        if ev:
            if isinstance(ev, RestEvent):
                current_bar.events.append(ev)
                bar_beats += ev.duration_beats
            else:
                midis = add_harmony([ev.midi], harmony, i)
                if len(midis) == 1:
                    current_bar.events.append(NoteEvent(midi=midis[0], duration_beats=ev.duration_beats, volume=ev.volume))
                else:
                    current_bar.events.append(ChordEvent(midis=midis, duration_beats=ev.duration_beats, volume=ev.volume))
                bar_beats += ev.duration_beats

    if current_bar.events or bar_beats > 0 or current_bar.dc or current_bar.fine:
        bars.append(current_bar)
    return bars, section_starts


def _parse_part_line(
    line: str,
    base_duration: float,
    beats_per_bar: float,
    no_bar_check: bool,
    tonality_offset: int,
) -> tuple[Part, list[int]]:
    part = Part()
    line = line.strip()
    if line.startswith("&"):
        line = line[1:].strip()
    part_octave = 0
    volume = 0.6
    if not no_bar_check:
        beats_per_bar = beats_per_bar
    else:
        beats_per_bar = 4.0
    # 提取 [8vb](...) 等；若含 \n\n（跨篇章合并）则不用 regex，直接解析
    m = re.match(r"\[(8vb|8va|15va|15vb)\]\s*\((.+)\)\s*", line, re.I | re.DOTALL) if "\n\n" not in line else None
    if m:
        kind = m.group(1).lower()
        inner = m.group(2).strip()
        if "8vb" in kind or "15vb" in kind:
            part_octave = -1 if "8" in kind else -2
        else:
            part_octave = 1 if "8" in kind else 2
        line = inner
    bars, section_starts = _parse_notation_scope(
        line, base_duration, beats_per_bar, part_octave, volume, False, 0, tonality_offset,
    )
    part.bars = bars
    return part, section_starts


def _bracket_depth(s: str) -> int:
    """计算字符串末尾的括号深度（[ ( 为 +1，] ) 为 -1）"""
    return sum(1 for c in s if c in "[(") - sum(1 for c in s if c in "])")


def _merge_part_lines(lines: list[str]) -> list[str]:
    """
    合并跨行的括号内容。括号可跨行/篇章，与小节非嵌套。
    当某行有未闭合括号时，与后续行合并直至平衡。
    """
    merged: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip().startswith("&"):
            i += 1
            continue
        content = line
        depth = _bracket_depth(content)
        j = i + 1
        while depth != 0 and j < len(lines):
            content += "\n" + lines[j]
            depth = _bracket_depth(content)
            j += 1
        merged.append(content)
        i = j
    return merged


def _split_sections(text: str) -> list[list[str]]:
    """
    按双换行分隔篇章。括号可跨行/篇章，与小节非嵌套关系。
    仅在括号深度为 0 时分割，避免将跨篇章的括号内容拆开。
    返回 sections，每个 section 的 part_lines 已合并跨行括号。
    """
    depth = 0
    blocks: list[str] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        if i < n - 1 and text[i] == "\n" and text[i + 1] == "\n" and depth == 0:
            chunk = text[start:i].strip()
            if chunk:
                blocks.append(chunk)
            i += 2
            while i < n and text[i] in "\n\t ":
                i += 1
            start = i
            continue
        if text[i] in "[(":
            depth += 1
        elif text[i] in "])":
            depth -= 1
        i += 1
    chunk = text[start:].strip()
    if chunk:
        blocks.append(chunk)
    # 合并：若某块仅为全局设定（无 | 小节线），与下一块合并，使 \tonality 等与后续小节同属一篇章
    merged_blocks: list[str] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        while i + 1 < len(blocks) and "|" not in block and block.strip().startswith("\\"):
            block = block + "\n\n" + blocks[i + 1]
            i += 1
        merged_blocks.append(block)
        i += 1
    sections: list[tuple[str, list[str]]] = []
    for block in merged_blocks:
        lines = block.split("\n")
        part_lines = _merge_part_lines(lines)
        # 单声部无 & 时，将含 | 的整块视为一个声部
        if not part_lines and "|" in block:
            part_lines = [block]
        if part_lines:
            sections.append((block, part_lines))
    return sections


def _split_parts(text: str) -> list[list[str]]:
    """兼容旧逻辑：不按篇章，仅按空行分块"""
    lines = text.split("\n")
    current_block = []
    all_blocks = []
    for line in lines:
        if line.strip().startswith("&"):
            current_block.append(line)
        else:
            if current_block:
                all_blocks.append(current_block)
                current_block = []
    if current_block:
        all_blocks.append(current_block)
    return all_blocks


def _settings_to_duration(settings: GlobalSettings) -> tuple[float, float]:
    """从 GlobalSettings 得到 base_duration 和 beats_per_bar"""
    if settings.no_bar_check:
        return 0.25, 4.0
    beat_unit = 1.0 / settings.beat_denominator
    return beat_unit, float(settings.beat_numerator)


def parse(text: str) -> ParsedScore:
    settings, rest = _parse_global(text)
    _check_brackets_raise(text)
    sections_raw = _split_sections(rest)
    if not sections_raw and rest.strip():
        base_duration, beats_per_bar = _settings_to_duration(settings)
        tonality_offset = _tonality_to_semitones(settings.tonality)
        part, _ = _parse_part_line(rest, base_duration, beats_per_bar, settings.no_bar_check, tonality_offset)
        if part.bars:
            return ParsedScore(
                settings=settings,
                parts=[part],
                sections=[[part]],
                section_settings=[settings],
            )
        return ParsedScore(settings=settings, parts=[], sections=[], section_settings=[])

    sections: list[list[Part]] = []
    section_settings_list: list[GlobalSettings] = []
    inherit = settings

    for block_str, _ in sections_raw:
        sec_settings, content = _parse_global(block_str, inherit)
        inherit = sec_settings
        base_duration, beats_per_bar = _settings_to_duration(sec_settings)
        tonality_offset = _tonality_to_semitones(sec_settings.tonality)

        # 从 content（已剥离 \tonality 等）提取 part_lines
        lines = content.split("\n")
        part_lines = _merge_part_lines(lines)
        if not part_lines and "|" in content:
            part_lines = [content]

        if not part_lines:
            continue

        sec_parts: list[Part] = []
        for line in part_lines:
            if line.strip().startswith("&"):
                line = line.strip()[1:].lstrip()
            part, _ = _parse_part_line(
                line, base_duration, beats_per_bar, sec_settings.no_bar_check, tonality_offset
            )
            if part.bars:
                sec_parts.append(part)

        if sec_parts:
            sections.append(sec_parts)
            section_settings_list.append(sec_settings)

    if not sections:
        return ParsedScore(settings=settings, parts=[], sections=[], section_settings=[])

    # 扁平化 parts：多篇章时按篇章顺序拼接
    flat_parts: list[Part] = []
    n_parts = max(len(sec) for sec in sections)
    for part_idx in range(n_parts):
        combined = Part(bars=[])
        for sec in sections:
            if part_idx < len(sec):
                combined.bars.extend(sec[part_idx].bars)
        if combined.bars:
            flat_parts.append(combined)

    return ParsedScore(
        settings=settings,
        parts=flat_parts,
        sections=sections,
        section_settings=section_settings_list,
    )
