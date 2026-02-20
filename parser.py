"""
简谱解析器：解析全局设定和简谱语法，支持括号跨小节。
错误由解析过程抛出 ParseError。
"""
import copy
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

# 调性偏移：bA=Ab, bB=Bb, bC=Cb 等，降号调根音 pitch class (0–11)
TONALITY_FLAT_OFFSET = {"C": 11, "D": 1, "E": 3, "F": 4, "G": 6, "A": 8, "B": 10}
# 1–7 对应 CDEFGAB，b+数字 的降号调半音偏移
TONALITY_FLAT_BY_DEGREE = {1: 11, 2: 1, 3: 3, 4: 4, 5: 6, 6: 8, 7: 10}
# #+数字 的升号调半音偏移
TONALITY_SHARP_BY_DEGREE = {1: 1, 2: 2, 3: 5, 4: 6, 5: 8, 6: 10, 7: 0}
# 字母 C–B 大调；#C–#B 升号调
TONALITY_LETTER_OFFSET = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
TONALITY_SHARP_LETTER = {"C": 1, "D": 3, "E": 5, "F": 6, "G": 8, "A": 10, "B": 0}

# 音量映射
VOLUME_MAP = {
    "fff": 1.0, "ff": 0.9, "f": 0.8, "mf": 0.7, "mp": 0.5, "p": 0.4, "pp": 0.3, "ppp": 0.2,
}


@dataclass
class GlobalSettings:
    tonality: str = "0"
    beat_numerator: int = 4
    beat_denominator: int = 4
    bpm: int = 120
    no_bar_check: bool = False


@dataclass
class NoteEvent:
    midi: int
    duration_beats: float
    volume: float = 0.6
    lyric: Optional[str] = None  # 行内歌词，如 1(啊)
    tied_to_next: bool = False
    tied_from_prev: bool = False
    arpeggio: bool = False  # [a] 琶音装饰：快速连续弹奏，从低到高，同时终止


@dataclass
class ChordEvent:
    midis: list[int]
    duration_beats: float
    volume: float = 0.6
    lyric: Optional[str] = None  # 行内歌词
    tied_to_next: bool = False
    tied_from_prev: bool = False
    tied_from_prev_midis: list[int] = field(default_factory=list)  # 和弦连音时，本和弦中哪些音与上一和弦相连
    arpeggio: bool = False  # [a] 琶音装饰：快速连续弹奏，从低到高，同时终止


@dataclass
class RestEvent:
    duration_beats: float


@dataclass
class BarContent:
    events: list = field(default_factory=list)
    dc: bool = False   # [dc] Da Capo：从此处跳回开头
    fine: bool = False  # [fine]：在此处结束
    tie_adjustment: float = 0.0  # 连音线跨小节：+ 表示从上一小节转入，- 表示转出到下一小节


@dataclass
class Part:
    bars: list[BarContent] = field(default_factory=list)


@dataclass
class TTSEvent:
    """TTS 事件：仅可插入在篇章之间"""
    text: str
    lang: str  # zh, ja, en
    voice_id: Optional[int] = None  # VOICEVOX style_id，有则用 VOICEVOX 不用 edge-tts


# [cello][guitar] 等乐器标记：对当前行及后续篇章同一声部生效
VALID_INSTRUMENT_TAGS = {"grand_piano", "piano", "violin", "cello", "trumpet", "clarinet", "oboe", "alto_sax", "tenor_sax", "bass", "guitar"}


def _strip_instrument_tag(line: str) -> tuple[str, Optional[str]]:
    """从行首剥离 [cello][guitar] 等乐器标记，返回 (剩余行, 乐器名或 None)。"""
    rest = line.lstrip()
    instrument: Optional[str] = None
    while rest.startswith("["):
        end = _find_matching_bracket(rest, 0)
        if end < 0:
            break
        inner = rest[1:end].strip().lower()
        if inner in VALID_INSTRUMENT_TAGS:
            instrument = "grand_piano" if inner == "piano" else inner
            rest = rest[end + 1 :].lstrip()
        else:
            break  # [8va][8vb] 等非乐器标记不剥离
    return rest, instrument


@dataclass
class ParsedScore:
    settings: GlobalSettings
    parts: list[Part] = field(default_factory=list)  # 兼容：扁平化所有篇章
    sections: list[list[Part]] = field(default_factory=list)  # 篇章列表，每篇章 & 数量相同
    section_settings: list[GlobalSettings] = field(default_factory=list)  # 每篇章的独立设定（覆盖前面的）
    section_tts: list[list[TTSEvent]] = field(default_factory=list)  # 每篇章前的 TTS，section_tts[i] 在 section i 之前播放
    section_lyrics: list[list[tuple[int, list[str], Optional[int], int]]] = field(default_factory=list)  # 每篇章的 \lyrics{(part_index, syllables, voice_id?, melody_part)}
    section_part_instruments: list[dict[int, str]] = field(default_factory=list)  # 每篇章每声部的乐器，part_index -> instrument


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
    """调性转半音偏移。数字=上下移半音数(0,1,-1,+2等)；字母+升降号为快捷写法(C=0,D=2,#C=1,bD=1等)"""
    t = tonality.strip()
    # 数字：直接表示上下移多少个半音
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


def parse_note_part_to_midi(part: str, octave: int = 4, tonality_offset: int = 0) -> Optional[int]:
    """将单个音符部分（如 1、.6、6.、#1）解析为 MIDI 音高。用于和弦排序等。"""
    part = part.strip().lstrip("~")
    if not part or part.startswith("0"):
        return None
    acc = 0
    if part.startswith("#"):
        acc = 1
        part = part[1:]
    elif part.startswith("b") and len(part) > 1 and part[1].isdigit():
        acc = -1
        part = part[1:]
    elif part.startswith("^"):
        acc = 2
        part = part[1:]
    left_dots = 0
    while part.startswith("."):
        left_dots += 1
        part = part[1:]
    part = re.sub(r"[-_]+$", "", part)
    right_dots = 0
    while part.endswith("."):
        right_dots += 1
        part = part[:-1]
    if not part or not part[0].isdigit():
        return None
    num = int(part[0])
    if num < 1 or num > 7:
        return None
    oct_final = octave - left_dots + right_dots
    return _simplified_to_midi(num, oct_final, acc, tonality_offset)


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
    prev_bars: list[BarContent] | None = None,
    arpeggio_scope: bool = False,  # [a](...) 时作用于括号内所有音
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
    repeat_start = 0  # |: 或最开头，用于 :| 重复
    content = content.strip()
    # 若整段被一对括号包裹（如 [8va](...) 的 scope 提取错误时），先剥掉外层括号
    if content.startswith("("):
        end_p = _find_matching_paren(content, 0)
        if end_p == len(content) - 1:
            content = content[1:-1].strip()
    n = len(content)
    prev_note_midi: Optional[int] = None  # 用于 ~ 连音线
    volume = default_volume
    tie_target_ev: Optional[NoteEvent | ChordEvent] = None  # ~ 跨小节时，后续 - 继续延长该音
    tie_target_bar: Optional[BarContent] = None
    next_arpeggio = False  # [a] 无括号时，作用于下一个音

    def flush_bar():
        nonlocal current_bar, bar_beats, tie_target_ev, tie_target_bar
        if current_bar.events or bar_beats > 0 or current_bar.dc or current_bar.fine:
            bars.append(current_bar)
        current_bar = BarContent()
        bar_beats = 0.0
        tie_target_ev = None
        tie_target_bar = None
        bar_accidentals.clear()

    def get_arpeggio_for_event() -> bool:
        """返回当前是否应用琶音，若为 next_arpeggio 则消费之"""
        nonlocal next_arpeggio
        arp = arpeggio_scope or next_arpeggio
        if next_arpeggio:
            next_arpeggio = False
        return arp

    def parse_note_token(tok: str, oct_off: int, acc: int, apply_arpeggio: bool = False) -> Optional[NoteEvent | RestEvent]:
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
        # 先剥离左侧八度点（. 表示低八度），再剥离升降号，否则 .b7 会因 b 在 . 后而漏识别
        core = tok
        left_dots = right_dots = 0
        while core.startswith("."):
            left_dots += 1
            core = core[1:]
        has_explicit_acc = False
        if core.startswith("#"):
            acc = 1
            has_explicit_acc = True
            core = core[1:]
        elif core.startswith("b") and len(core) > 1 and core[1].isdigit():
            acc = -1
            has_explicit_acc = True
            core = core[1:]
        elif core.startswith("^"):
            acc = 2
            has_explicit_acc = True
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
        if not has_explicit_acc:
            acc = bar_accidentals.get(num, 0) if deviation_explicit else 0
        else:
            if deviation_explicit:
                bar_accidentals[num] = acc
        dur = base_duration * (1 + ext) * (0.5**shrt)  # - 增加一拍，非乘2
        oct_final = part_octave - left_dots + right_dots
        midi = _simplified_to_midi(num, oct_final, acc, tonality_offset)
        return NoteEvent(midi=midi, duration_beats=dur, volume=volume, arpeggio=apply_arpeggio)

    bar_accidentals: dict[int, int] = {}  # 小节内各音级的临时升降号：1-7 -> 0/1/-1/2

    def parse_accidental(tok: str) -> int:
        """从 token 解析升降号。"""
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
        # |: 重复起点
        if i + 1 < n and content[i : i + 2] == "|:":
            flush_bar()
            repeat_start = len(bars)
            i += 2
            continue
        # :| 重复终点：将 repeat_start 到当前的小节重复一次
        # :|: 特例：结束当前重复后立即开始新重复（第一结尾 + 第二开头）
        if i + 1 < n and content[i : i + 2] == ":|":
            flush_bar()
            seg = bars[repeat_start:]
            if seg:
                for b in seg:
                    bars.append(copy.deepcopy(b))
            if i + 2 < n and content[i : i + 3] == ":|:":
                repeat_start = len(bars)
                i += 3
            else:
                i += 2
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
            depth -= 1
            if notation:
                notation_lower = notation.lower()
                prev_harmony = harmony
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
                elif notation_lower in ("a", "arpeggio"):
                    if scope is None:
                        next_arpeggio = True
                if scope is not None:
                    sub_arpeggio = notation_lower in ("a", "arpeggio")
                    sub_bars, _ = _parse_notation_scope(
                        scope, base_duration, beats_per_bar,
                        scope_octave, volume, deviation_explicit, harmony, tonality_offset,
                        arpeggio_scope=sub_arpeggio,
                    )
                    if rest_starts_with_double_newline:
                        section_starts.append(len(bars) + len(sub_bars))
                    if apply_underscore:
                        _halve_bar_events(sub_bars)
                    # 将当前小节（如 0 - 0）与 scope 第一小节合并
                    if current_bar.events or bar_beats > 0:
                        if sub_bars:
                            sub_bars[0].events = current_bar.events + sub_bars[0].events
                        else:
                            bars.append(current_bar)
                    # 括号在小节中中止时，) 后到第一个 | 的内容与 scope 最后一小节合并
                    if rest_after and sub_bars and not rest_after.lstrip().startswith("|"):
                        if "|" in rest_after:
                            rest_to_merge, rest_remain = rest_after.split("|", 1)
                            rest_to_merge = rest_to_merge.rstrip()
                            rest_remain = "|" + rest_remain
                        else:
                            rest_to_merge = rest_after
                            rest_remain = ""
                        if rest_to_merge.strip():
                            # rest_after 在 scope 外，和声记号 [+3][-3] 等不作用于此处
                            merge_harmony = prev_harmony if (notation_lower and re.match(r"^[+-]?[35]$", notation_lower.replace(" ", ""))) else harmony
                            rest_bars, _ = _parse_notation_scope(
                                rest_to_merge, base_duration, beats_per_bar,
                                part_octave, volume, deviation_explicit, merge_harmony, tonality_offset,
                            )
                            if rest_bars:
                                sub_bars[-1].events.extend(rest_bars[0].events)
                                for rb in rest_bars[1:]:
                                    sub_bars.append(rb)
                        consumed += len(rest_to_merge)
                    for sb in sub_bars:
                        bars.append(sb)
                    current_bar = BarContent()
                    bar_beats = 0.0
                    # [+3][-3][+5][-5] 有 scope 时，和声仅作用于 scope 内，结束后恢复
                    if notation_lower and re.match(r"^[+-]?[35]$", notation_lower.replace(" ", "")):
                        harmony = prev_harmony
            i += consumed
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
            notes_in_tuplet: list[tuple[str, list[int], float, bool]] = []  # (kind, midis, _, tied_from_prev)
            for t in tokens:
                if not t or t in "-_":
                    continue
                tied_from_prev = t.startswith("~")
                t_clean = t.lstrip("~")  # 连音线 ~ 不影响时值，只做渲染
                if "/" in t:
                    parts = t.split("/")
                    chord = []
                    for p in parts:
                        ev = parse_note_token(p.lstrip("~"), part_octave, 0)
                        if ev and isinstance(ev, NoteEvent):
                            chord.append(ev.midi)
                    if chord:
                        notes_in_tuplet.append(("chord", chord, base_duration, tied_from_prev))
                else:
                    ev = parse_note_token(t_clean, part_octave, 0)
                    if ev and isinstance(ev, NoteEvent):
                        notes_in_tuplet.append(("note", [ev.midi], ev.duration_beats, tied_from_prev))
                    elif ev and isinstance(ev, RestEvent):
                        notes_in_tuplet.append(("rest", [], ev.duration_beats, tied_from_prev))
            if notes_in_tuplet:
                # 均分为 default；(notes)_：每个音为八分；(notes)n：显式 n
                if not has_explicit_n:
                    n_val = len(notes_in_tuplet)
                if apply_underscore:
                    each = base_duration / 2  # _ 等同于填写2，四分除以2=八分
                else:
                    each = base_duration / n_val
                # 连续连音线 ~3. ~3. 时，第一个 ~ 需连到括号前音符
                prev_ev: NoteEvent | ChordEvent | None = current_bar.events[-1] if current_bar.events else None
                for kind, midis, _, tied_from_prev in notes_in_tuplet:
                    if kind == "rest":
                        current_bar.events.append(RestEvent(duration_beats=each))
                        prev_ev = None
                    else:
                        midis = add_harmony(midis, harmony, i)
                        if tied_from_prev and prev_ev is not None:
                            prev_ev.tied_to_next = True
                        if kind == "chord":
                            ev = ChordEvent(midis=midis, duration_beats=each, volume=volume, tied_from_prev=tied_from_prev, arpeggio=get_arpeggio_for_event())
                            current_bar.events.append(ev)
                            prev_ev = ev
                        elif len(midis) == 1:
                            ev = NoteEvent(midi=midis[0], duration_beats=each, volume=volume, tied_from_prev=tied_from_prev, arpeggio=get_arpeggio_for_event())
                            current_bar.events.append(ev)
                            prev_ev = ev
                        else:
                            ev = ChordEvent(midis=midis, duration_beats=each, volume=volume, tied_from_prev=tied_from_prev, arpeggio=get_arpeggio_for_event())
                            current_bar.events.append(ev)
                            prev_ev = ev
                    bar_beats += each
            continue
        # 8 重复上小节（可跨篇章使用上一篇章最后一小节）
        if content[i] == "8" and (i + 1 >= n or content[i + 1] in " \t\n|"):
            src_bar = None
            if bars:
                src_bar = bars[-1]
            elif prev_bars:
                src_bar = prev_bars[-1]
            if src_bar:
                current_bar = BarContent(events=[e for e in src_bar.events])
                bar_beats = sum(
                    e.duration_beats for e in current_bar.events
                    if isinstance(e, (NoteEvent, ChordEvent, RestEvent))
                )
            i += 1
            continue
        # 收集 token（不含行内歌词括号）
        j = i
        while j < n and content[j] not in " \t\n|[]()":
            j += 1
        tok = content[i:j]
        i = j
        inline_lyric: Optional[str] = None
        rest_after = content[i:].lstrip()
        paren_start = i + len(content[i:]) - len(rest_after)
        if rest_after.startswith("(") and paren_start < n:
            end_p = _find_matching_paren(content, paren_start)
            if end_p >= 0:
                inner = content[paren_start + 1 : end_p].strip()
                if inner and not re.search(r"[0-9|\[\]]", inner):
                    inline_lyric = inner
                    i = end_p + 1

        if not tok:
            continue
        if tok == "-":
            if current_bar.events:
                last = current_bar.events[-1]
                if hasattr(last, "duration_beats"):
                    last.duration_beats += base_duration  # 增加一拍
                    bar_beats += base_duration
            elif tie_target_ev is not None and tie_target_bar is not None:
                # ~ 跨小节后，- 继续延长上一小节最后一音
                tie_target_ev.duration_beats += base_duration
                tie_target_bar.tie_adjustment -= base_duration
                current_bar.tie_adjustment += base_duration
                bar_beats += base_duration
            continue
        if tok == "_":
            if current_bar.events:
                last = current_bar.events[-1]
                if hasattr(last, "duration_beats"):
                    last.duration_beats /= 2
                    bar_beats -= getattr(last, "duration_beats", 0)
            continue
        # ~ 连音线：两个音符都渲染，用连音弧线连接（可连到上一个小节）
        if tok.startswith("~"):
            tok = tok[1:]  # 仅去掉首 ~，保留 .5 等八度点
            last_ev = current_bar.events[-1] if current_bar.events else None
            last_in_current = last_ev is not None
            if last_ev is None and bars:
                prev_bar = bars[-1]
                if prev_bar.events:
                    last_ev = prev_bar.events[-1]
            add_dur = base_duration
            if "/" in tok:
                parts = tok.split("/")
                chord = []
                max_dur = base_duration
                for p in parts:
                    p_clean = p.lstrip("~")  # 每部分可带 ~，如 ~1 表示 1
                    ev = parse_note_token(p_clean, part_octave, 0)
                    if ev and isinstance(ev, NoteEvent):
                        chord.append(ev.midi)
                        max_dur = max(max_dur, ev.duration_beats)
                add_dur = max_dur  # 连音和弦取各音最大时值（支持 ~.5/~1--- 等）
                if tok.rstrip().endswith("_"):
                    add_dur = base_duration / 2  # ~2/5/7_ 整和弦为八分
                acc_tok = _any_accidental_in_tokens(parts) if harmony != 0 else None
                if acc_tok:
                    pos = content.rfind(acc_tok, 0, i + len(tok) + 1)
                    if pos < 0:
                        pos = i
                    line, col = _pos_to_line_col(content, pos)
                    raise ParseError(line, col, "和声记号 [+3][-3][+5][-5] 不可用于含升降号的音符", pos, pos + len(acc_tok))
                chord_midis = add_harmony(chord, harmony, i)  # [-3] 等和声作用于连音和弦
                if chord_midis and last_ev is not None:
                    last_ev.tied_to_next = True
                    tied_midis = [m for m in chord_midis if m in getattr(last_ev, "midis", [last_ev.midi] if hasattr(last_ev, "midi") else [])]
                    new_ev = ChordEvent(midis=chord_midis, duration_beats=add_dur, volume=last_ev.volume, lyric=inline_lyric, tied_from_prev=True, tied_from_prev_midis=tied_midis, arpeggio=get_arpeggio_for_event())
                    current_bar.events.append(new_ev)
                    bar_beats += add_dur
                    if last_in_current:
                        tie_target_ev = None
                        tie_target_bar = None
                    else:
                        tie_target_ev = new_ev
                        tie_target_bar = current_bar
            else:
                ev = parse_note_token(tok, part_octave, 0)
                if ev and isinstance(ev, NoteEvent) and last_ev is not None:
                    add_dur = ev.duration_beats
                    # 仅同音高时连音；[-3] 等和声作用域内需输出 ChordEvent 以正确匹配
                    if isinstance(last_ev, NoteEvent) and last_ev.midi == ev.midi:
                        last_ev.tied_to_next = True
                        new_ev = NoteEvent(midi=ev.midi, duration_beats=add_dur, volume=ev.volume, lyric=inline_lyric, tied_from_prev=True, arpeggio=get_arpeggio_for_event())
                        current_bar.events.append(new_ev)
                        bar_beats += add_dur
                        if last_in_current:
                            tie_target_ev = None
                            tie_target_bar = None
                        else:
                            tie_target_ev = new_ev
                            tie_target_bar = current_bar
                    elif isinstance(last_ev, ChordEvent) and ev.midi in last_ev.midis:
                        last_ev.tied_to_next = True
                        midis = add_harmony([ev.midi], harmony, i)
                        tied_midis = [m for m in midis if m in last_ev.midis]
                        new_ev = ChordEvent(midis=midis, duration_beats=add_dur, volume=last_ev.volume, lyric=inline_lyric, tied_from_prev=True, tied_from_prev_midis=tied_midis, arpeggio=get_arpeggio_for_event())
                        current_bar.events.append(new_ev)
                        bar_beats += add_dur
                        if last_in_current:
                            tie_target_ev = None
                            tie_target_bar = None
                        else:
                            tie_target_ev = new_ev
                            tie_target_bar = current_bar
                elif ev and isinstance(ev, RestEvent):
                    current_bar.events.append(ev)
                    bar_beats += ev.duration_beats
                    tie_target_ev = None
                    tie_target_bar = None
            continue
        # 和弦 1/3/5（每个音可单独带升降号）；末尾 _ 表示四分除以2=八分
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
            # 和弦末尾 _（如 3/5_）表示整和弦为八分
            if tok.rstrip().endswith("_"):
                max_dur = base_duration / 2
            chord_midis = add_harmony(chord_midis, harmony, i)
            if chord_midis:
                current_bar.events.append(ChordEvent(midis=chord_midis, duration_beats=max_dur, volume=volume, lyric=inline_lyric, arpeggio=get_arpeggio_for_event()))
                bar_beats += max_dur
                tie_target_ev = None
                tie_target_bar = None
            continue
        # 单音
        if harmony != 0 and _has_accidental(tok):
            line, col = _pos_to_line_col(content, i)
            raise ParseError(line, col, "和声记号 [+3][-3][+5][-5] 不可用于含升降号的音符", i, i + len(tok))
        acc = parse_accidental(tok)
        ev = parse_note_token(tok, part_octave, acc, apply_arpeggio=get_arpeggio_for_event())
        if ev:
            if isinstance(ev, RestEvent):
                current_bar.events.append(ev)
                bar_beats += ev.duration_beats
            else:
                midis = add_harmony([ev.midi], harmony, i)
                if len(midis) == 1:
                    current_bar.events.append(NoteEvent(midi=midis[0], duration_beats=ev.duration_beats, volume=ev.volume, lyric=inline_lyric, arpeggio=getattr(ev, "arpeggio", False)))
                else:
                    current_bar.events.append(ChordEvent(midis=midis, duration_beats=ev.duration_beats, volume=ev.volume, lyric=inline_lyric, arpeggio=getattr(ev, "arpeggio", False)))
                bar_beats += ev.duration_beats
            tie_target_ev = None
            tie_target_bar = None

    if current_bar.events or bar_beats > 0 or current_bar.dc or current_bar.fine:
        bars.append(current_bar)
    return bars, section_starts


def _parse_part_line(
    line: str,
    base_duration: float,
    beats_per_bar: float,
    no_bar_check: bool,
    tonality_offset: int,
    prev_bars: list[BarContent] | None = None,
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
        line, base_duration, beats_per_bar, part_octave, volume, False, 0, tonality_offset, prev_bars,
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
    # \tts 仅可插入篇章之间，不参与合并
    merged_blocks: list[str] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        while i + 1 < len(blocks) and "|" not in block and block.strip().startswith("\\") and "\\tts" not in block.lower():
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
        # 包含所有块（含仅 \tts 的块，part_lines 为空）
        sections.append((block, part_lines))
    return sections

def _extract_lyrics(content: str) -> tuple[str, list[tuple[int, list[str], Optional[int], int]]]:
    """
    从 content 中提取 \\lyrics{...}、\\lyrics{...}{part_index}、\\lyrics{...}{part_index}{voice_id}、\\lyrics{...}{part_index}{voice_id}{melody}。
    返回 (剩余内容, [(part_index, syllables, voice_id, melody_part)])。
    melody_part: 0=第一音旋律 1=第二音旋律（和声时）
    """
    result: list[tuple[int, list[str], Optional[int], int]] = []
    rest = content

    def replacer(m: re.Match) -> str:
        text = m.group(1).strip()
        part_str = m.group(2)
        voice_str = m.group(3)
        melody_str = m.group(4)
        part_index = int(part_str.strip()) if part_str else 0
        voice_id = None
        if voice_str:
            try:
                voice_id = int(voice_str.strip())
            except ValueError:
                pass
        melody_part = 0
        if melody_str is not None:
            try:
                melody_part = int(melody_str.strip())
                if melody_part not in (0, 1):
                    melody_part = 0
            except ValueError:
                pass
        syllables = [s.strip() for s in text.split("/") if s.strip()]
        if syllables:
            result.append((part_index, syllables, voice_id, melody_part))
        return ""

    pattern = re.compile(r"\\lyrics\{([^{}]*)\}(?:\{(\d+)\})?(?:\{([^{}]+)\})?(?:\{([01])\})?\s*", re.I)
    new_content = pattern.sub(replacer, rest)
    return new_content, result


def _extract_tts(block: str) -> list[TTSEvent]:
    """从块中提取 \\tts{text}、\\tts{text}{lang}、\\tts{text}{lang}{voice_id}"""
    result: list[TTSEvent] = []
    rest = block
    while rest:
        m = re.search(r"\\tts\{([^{}]*)\}(?:\{([^{}]+)\})?(?:\{([^{}]+)\})?\s*", rest, re.I)
        if not m:
            break
        text = m.group(1).strip()
        raw = (m.group(2) or "en").strip().lower()[:2]
        lang = "zh-CN" if raw == "zh" else "ja-JP" if raw == "ja" else "en-US"
        voice_id = None
        if m.group(3):
            try:
                voice_id = int(m.group(3).strip())
            except ValueError:
                pass
        if text:
            result.append(TTSEvent(text=text, lang=lang, voice_id=voice_id))
        rest = rest[m.end():]
    return result


def _settings_to_duration(settings: GlobalSettings) -> tuple[float, float]:
    """从 GlobalSettings 得到 base_duration 和 beats_per_bar"""
    if settings.no_bar_check:
        return 0.25, 4.0
    beat_unit = 1.0 / settings.beat_denominator
    return beat_unit, float(settings.beat_numerator)


def _strip_comments(text: str) -> str:
    """移除 // 单行注释（同 Java），解析时忽略注释内容"""
    return re.sub(r"//[^\n]*", "", text)


def _extract_and_expand_defines(text: str) -> str:
    """
    提取 \\define{key}{value} 并将文中所有 [key] 替换为 value。
    只有方括号中的内容在 define 中定义时才会被替换；未定义的 [xxx] 保持原样。
    """
    defines: dict[str, str] = {}
    rest = text

    # 1. 提取所有 \define{key}{value}
    define_pattern = re.compile(r"\\define\{([^{}]+)\}\{([^{}]*)\}\s*", re.I)
    while True:
        m = define_pattern.search(rest)
        if not m:
            break
        key = m.group(1).strip()
        value = m.group(2)
        if key:
            defines[key] = value
        rest = rest[: m.start()] + rest[m.end() :]

    if not defines:
        return text

    # 2. 替换 [key] 为 value（仅当 key 在 defines 中）
    def replacer(m: re.Match) -> str:
        inner = m.group(1).strip()
        return defines.get(inner, m.group(0))

    return re.sub(r"\[([^\]]+)\]", replacer, rest)


def parse(text: str) -> ParsedScore:
    text = _strip_comments(text)
    text = _extract_and_expand_defines(text)
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
                section_part_instruments=[{0: "grand_piano"}],
            )
        return ParsedScore(settings=settings, parts=[], sections=[], section_settings=[], section_part_instruments=[])

    sections: list[list[Part]] = []
    section_settings_list: list[GlobalSettings] = []
    section_tts_list: list[list[TTSEvent]] = []
    section_lyrics_list: list[list[tuple[int, list[str], Optional[int], int]]] = []
    section_part_instruments_list: list[dict[int, str]] = []
    pending_tts: list[TTSEvent] = []
    inherit = settings

    for block_str, _ in sections_raw:
        # TTS 仅可插入篇章之间：若块仅含 \tts（无小节线、无 & 旋律），提取并等待下一篇章
        tts_events = _extract_tts(block_str)
        part_lines_check = _merge_part_lines(block_str.split("\n"))
        if tts_events and "|" not in block_str and not part_lines_check:
            pending_tts.extend(tts_events)
            # 仍解析全局设定以更新 inherit（如 \tonality 等）
            sec_settings, _ = _parse_global(block_str, inherit)
            inherit = sec_settings
            continue

        sec_settings, content = _parse_global(block_str, inherit)
        inherit = sec_settings
        content, block_lyrics = _extract_lyrics(content)
        base_duration, beats_per_bar = _settings_to_duration(sec_settings)
        tonality_offset = _tonality_to_semitones(sec_settings.tonality)

        # 从 content（已剥离 \tonality、\lyrics 等）提取 part_lines
        lines = content.split("\n")
        part_lines = _merge_part_lines(lines)
        if not part_lines:
            # 有 | 时整块为一声部；无小节模式（\no_bar_check）无 | 时，整块内容亦作为单声部
            if "|" in content or content.strip():
                part_lines = [content]

        if not part_lines:
            continue

        # 本篇章前的 TTS：前序块中的 + 本块中的 \tts（同块内 \tts 在旋律前）
        section_tts_list.append(pending_tts + tts_events)
        pending_tts = []

        # 上一篇章各声部的 bars，供 |8| 跨篇章重复
        prev_bars_per_part: list[list[BarContent]] = []
        if sections:
            n_prev_parts = max(len(p) for p in sections)
            prev_bars_per_part = [sections[-1][pi].bars if pi < len(sections[-1]) else [] for pi in range(n_prev_parts)]

        # 继承上一篇章的乐器；[cello] 等标记对当前行及后续篇章同声部生效
        part_instruments: dict[int, str] = {}
        if section_part_instruments_list:
            n_prev = max(section_part_instruments_list[-1].keys()) + 1
            part_instruments = {i: section_part_instruments_list[-1].get(i, "grand_piano") for i in range(n_prev)}

        sec_parts: list[Part] = []
        for part_idx, line in enumerate(part_lines):
            if line.strip().startswith("&"):
                line = line.strip()[1:].lstrip()
            # 剥离行首 [cello][guitar] 等乐器标记
            line, inst_tag = _strip_instrument_tag(line)
            if inst_tag:
                part_instruments[part_idx] = inst_tag
            prev_bars = prev_bars_per_part[part_idx] if part_idx < len(prev_bars_per_part) else None
            part, _ = _parse_part_line(
                line, base_duration, beats_per_bar, sec_settings.no_bar_check, tonality_offset, prev_bars,
            )
            if part.bars:
                sec_parts.append(part)

        if sec_parts:
            for part_idx in range(len(sec_parts)):
                if part_idx not in part_instruments:
                    part_instruments[part_idx] = "grand_piano"
            sections.append(sec_parts)
            section_settings_list.append(sec_settings)
            section_lyrics_list.append(block_lyrics)
            section_part_instruments_list.append(part_instruments)

    if not sections:
        return ParsedScore(settings=settings, parts=[], sections=[], section_settings=[], section_lyrics=[], section_part_instruments=[])

    # 末尾的 TTS（最后一篇章之后）
    if pending_tts:
        section_tts_list.append(pending_tts)

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
        section_tts=section_tts_list,
        section_lyrics=section_lyrics_list,
        section_part_instruments=section_part_instruments_list,
    )
