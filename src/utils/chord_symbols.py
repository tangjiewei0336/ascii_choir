r"""
和弦符号解析：支持级数（I、V7、vii°）和根音（G7、Cdim）两种写法。
返回简谱音级记号（1、3、5、b7 等），供伴奏、\define 等使用。
"""
import re
from typing import Optional

from src.core.parser import (
    C_MAJOR_BASE,
    _tonality_to_semitones,
    parse_note_part_to_midi,
)

# 和弦类型 -> 音程（相对根音的半音数）
CHORD_INTERVALS: dict[str, list[int]] = {
    "maj": [0, 4, 7],
    "M": [0, 4, 7],
    "m": [0, 3, 7],
    "min": [0, 3, 7],
    "dim": [0, 3, 6],
    "°": [0, 3, 6],
    "o": [0, 3, 6],  # o 与 ° 等价
    "aug": [0, 4, 8],
    "+": [0, 4, 8],
    "7": [0, 4, 7, 10],
    "V7": [0, 4, 7, 10],
    "maj7": [0, 4, 7, 11],
    "M7": [0, 4, 7, 11],
    "m7": [0, 3, 7, 10],
    "min7": [0, 3, 7, 10],
    "dim7": [0, 3, 6, 9],
    "°7": [0, 3, 6, 9],
    "o7": [0, 3, 6, 9],  # o7 与 °7 等价
    "m7b5": [0, 3, 6, 10],
}

ROMAN_TO_DEGREE: dict[str, int] = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
}

NOTE_TO_PC: dict[str, int] = {
    "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11,
}

# pitch class -> (degree, accidental: 0=natural, 1=#, -1=b)
# 等音优先用降号（b6 而非 #5）以便与和弦类型一致
PC_TO_DEGREE_ACC: dict[int, tuple[int, int]] = {
    0: (1, 0), 1: (1, 1),   # C, C#
    2: (2, 0), 3: (3, -1),  # D, Eb
    4: (3, 0), 5: (4, 0),   # E, F
    6: (5, -1), 7: (5, 0),  # Gb/F#, b5 或 #4，取 b5 便于和弦
    8: (6, -1), 9: (6, 0),  # Ab, A
    10: (7, -1), 11: (7, 0), # Bb, B
}


def _midi_to_notation(midi: int, tonality_offset: int, base_octave: int = 4) -> str:
    """将 MIDI 转为简谱记号（含八度点）"""
    # midi 相对于 C4=60 的偏移
    rel = midi - 60 - tonality_offset
    pc = rel % 12
    oct_delta = rel // 12
    if pc < 0:
        pc += 12
    if oct_delta < 0 and pc != 0:
        pc -= 12
        oct_delta += 1
        if pc < 0:
            pc += 12
    deg, acc = PC_TO_DEGREE_ACC.get(pc % 12, (1, 0))
    oct_final = base_octave + oct_delta
    left_dots = max(0, 4 - oct_final)
    right_dots = max(0, oct_final - 4)
    acc_str = "#" if acc == 1 else ("b" if acc == -1 else "")
    return "." * left_dots + acc_str + str(deg) + "." * right_dots


def parse_chord_symbol(symbol: str, tonality_offset: int = 0) -> Optional[list[str]]:
    """
    解析和弦符号，返回简谱音级列表（如 ["5","7","2.","4."]）。
    支持：
    - 级数：I, Imaj, iim, V7, vii°, IVmaj7（级数时可省略 maj 和 m）
    - 根音：C, Gm, G7, Bdim7, F#aug, Abm7
    """
    s = symbol.strip()
    if not s:
        return None

    root_pc: Optional[int] = None
    base_octave = 4

    # 级数：I, ii, III, iv, V, VI, vii, #IV, bVII 等
    roman_match = re.match(r"^(#{1,2}|b{1,2})?([IViv]+)\s*", s, re.I)
    if roman_match:
        acc_str = roman_match.group(1) or ""
        roman = roman_match.group(2)
        if roman.upper() in ROMAN_TO_DEGREE:
            deg = ROMAN_TO_DEGREE[roman.upper()]
            acc_semi = acc_str.count("#") - acc_str.count("b")
            root_pc = (C_MAJOR_BASE[deg] - 60 + tonality_offset + acc_semi) % 12
            if root_pc < 0:
                root_pc += 12
            s = s[roman_match.end():].strip()
        else:
            return None
    else:
        # 根音字母：C, C#, D#, Eb, F##, Gb（升降号可在字母前或后）
        note_match = re.match(r"^(#{1,2}|b{1,2})?([A-Ga-g])(#{1,2}|b{1,2})?\s*", s, re.I)
        if note_match:
            acc_before = note_match.group(1) or ""
            note = note_match.group(2).upper()
            acc_after = note_match.group(3) or ""
            acc_str = acc_before + acc_after
            root_pc = (NOTE_TO_PC.get(note, 0) + acc_str.count("#") - acc_str.count("b")) % 12
            s = s[note_match.end():].strip()
        else:
            return None

    # 和弦类型
    chord_type = "maj"
    if roman_match and roman_match.group(2)[0].islower() and not s:
        chord_type = "m"
    elif s:
        # 优先匹配多字符类型；m（小三）须在 M（大三）之前，否则 Cm 会误匹配为 CM
        # m7/min7 须在 M7/maj7 之前，否则 Cm7 会误匹配为 CM7
        # m7b5 须在 m7 之前，否则 A#m7b5 会误匹配为 A#m7
        order = ["dim7", "°7", "o7", "m7b5", "min7", "m7", "maj7", "M7", "dim", "°", "o", "aug", "+",
                 "maj", "min", "m", "M", "7", "V7"]
        for ct in order:
            if ct not in CHORD_INTERVALS:
                continue
            if ct in "°+":
                if s.startswith(ct):
                    chord_type = ct
                    s = s[len(ct):].lstrip()
                    break
            elif len(ct) <= len(s):
                # m 与 M 区分大小写：Cm=小三，CM=大三；m7 与 M7 同理
                if ct == "M" and s[:1] == "m" and len(s) == 1:
                    continue
                if ct == "m" and s[:1] == "M":
                    continue
                if ct == "M7" and s[:2] == "m7":
                    continue
                if ct == "m7" and s[:2] == "M7":
                    continue
                if s[:len(ct)].lower() == ct.lower():
                    chord_type = ct
                    s = s[len(ct):].lstrip()
                    break
        if s and s[0] in "°o":
            chord_type = "dim7" if "7" in chord_type else "dim"
            s = s[1:].lstrip()

    if chord_type not in CHORD_INTERVALS:
        chord_type = "maj"

    intervals = CHORD_INTERVALS[chord_type]
    root_midi = 60 + root_pc

    chord_midis = [root_midi + semi for semi in intervals]

    # 转位：低音作为最低音，其余按音高依次上行（如 C/G -> 5 1. 3.）
    if "/" in symbol:
        parts = symbol.split("/", 1)
        if len(parts) == 2:
            bass_str = parts[1].strip()
            bass_pc = parse_note_to_pc(bass_str)
            if bass_pc is not None:
                bass_idx = next((i for i, m in enumerate(chord_midis) if m % 12 == bass_pc), None)
                if bass_idx is not None:
                    bass_midi = chord_midis[bass_idx]
                    others = [(j, m) for j, m in enumerate(chord_midis) if j != bass_idx]
                    min_others = min(m for _, m in others) if others else bass_midi
                    while bass_midi >= min_others:
                        bass_midi -= 12
                    chord_midis[bass_idx] = bass_midi
                    # 其余音放在低音上方八度，按 pitch 上行排列（如 C/G -> 5 1. 3.）
                    prev = bass_midi + 12
                    for j, m in sorted(others, key=lambda x: (x[1] - bass_pc) % 12):
                        while chord_midis[j] < prev:
                            chord_midis[j] += 12
                        prev = chord_midis[j]
                    chord_midis.sort()

    result = [_midi_to_notation(m, tonality_offset, 4) for m in chord_midis]
    return result


def get_chord_pitch_classes(symbol: str, tonality_offset: int = 0) -> Optional[frozenset[int]]:
    """
    返回和弦的 pitch class 集合（0–11）。
    用于校验转位低音是否为和弦音。
    """
    s = symbol.strip()
    if not s:
        return None
    root_pc: Optional[int] = None
    roman_match = re.match(r"^(#{1,2}|b{1,2})?([IViv]+)\s*", s, re.I)
    if roman_match:
        acc_str = roman_match.group(1) or ""
        roman = roman_match.group(2)
        if roman.upper() in ROMAN_TO_DEGREE:
            deg = ROMAN_TO_DEGREE[roman.upper()]
            acc_semi = acc_str.count("#") - acc_str.count("b")
            root_pc = (C_MAJOR_BASE[deg] - 60 + tonality_offset + acc_semi) % 12
            if root_pc < 0:
                root_pc += 12
            s = s[roman_match.end():].strip()
        else:
            return None
    else:
        note_match = re.match(r"^(#{1,2}|b{1,2})?([A-Ga-g])(#{1,2}|b{1,2})?\s*", s, re.I)
        if note_match:
            acc_before = note_match.group(1) or ""
            note = note_match.group(2).upper()
            acc_after = note_match.group(3) or ""
            acc_str = acc_before + acc_after
            root_pc = (NOTE_TO_PC.get(note, 0) + acc_str.count("#") - acc_str.count("b")) % 12
            s = s[note_match.end():].strip()
        else:
            return None
    chord_type = "maj"
    if roman_match and roman_match.group(2)[0].islower() and not s:
        chord_type = "m"
    elif s:
        # m7/min7 须在 M7/maj7 之前，否则 Cm7 会误匹配为 CM7
        # m7b5 须在 m7 之前，否则 A#m7b5 会误匹配为 A#m7
        order = ["dim7", "°7", "o7", "m7b5", "min7", "m7", "maj7", "M7", "dim", "°", "o", "aug", "+",
                 "maj", "min", "m", "M", "7", "V7"]
        for ct in order:
            if ct not in CHORD_INTERVALS:
                continue
            if ct in "°+":
                if s.startswith(ct):
                    chord_type = ct
                    s = s[len(ct):].lstrip()
                    break
            elif len(ct) <= len(s):
                if ct == "M" and s[:1] == "m" and len(s) == 1:
                    continue
                if ct == "m" and s[:1] == "M":
                    continue
                if ct == "M7" and len(s) >= 2 and s[:2] == "m7":
                    continue
                if ct == "m7" and len(s) >= 2 and s[:2] == "M7":
                    continue
                if s[:len(ct)].lower() == ct.lower():
                    chord_type = ct
                    s = s[len(ct):].lstrip()
                    break
        if s and s[0] in "°o":
            chord_type = "dim7" if "7" in chord_type else "dim"
    intervals = CHORD_INTERVALS.get(chord_type, [0, 4, 7])
    return frozenset((root_pc + i) % 12 for i in intervals)


def get_chord_root_pc(symbol: str, tonality_offset: int = 0) -> Optional[int]:
    """解析和弦符号，返回根音的 pitch class（0–11）。用于转位时排除根音。"""
    pcs = get_chord_pitch_classes(symbol.split("/")[0].strip(), tonality_offset)
    if not pcs:
        return None
    # 根音为 intervals[0] 对应的 pc，即最小半音间隔对应的音
    base = symbol.split("/")[0].strip()
    s = base
    root_pc = None
    roman_match = re.match(r"^(#{1,2}|b{1,2})?([IViv]+)\s*", s, re.I)
    if roman_match:
        acc_str = roman_match.group(1) or ""
        roman = roman_match.group(2)
        if roman.upper() in ROMAN_TO_DEGREE:
            deg = ROMAN_TO_DEGREE[roman.upper()]
            acc_semi = acc_str.count("#") - acc_str.count("b")
            root_pc = (C_MAJOR_BASE[deg] - 60 + tonality_offset + acc_semi) % 12
            if root_pc < 0:
                root_pc += 12
    else:
        note_match = re.match(r"^(#{1,2}|b{1,2})?([A-Ga-g])(#{1,2}|b{1,2})?\s*", s, re.I)
        if note_match:
            acc_before = note_match.group(1) or ""
            note = note_match.group(2).upper()
            acc_after = note_match.group(3) or ""
            acc_str = acc_before + acc_after
            root_pc = (NOTE_TO_PC.get(note, 0) + acc_str.count("#") - acc_str.count("b")) % 12
    return root_pc


def parse_note_to_pc(note_str: str) -> Optional[int]:
    """将音符名（C、Eb、F#、G# 等）转为 pitch class（0–11）"""
    s = note_str.strip()
    if not s:
        return None
    # 支持 #/b 在字母前或后：C#, #C, Eb, bE
    m = re.match(r"^(#{1,2}|b{1,2})?([A-Ga-g])(#{1,2}|b{1,2})?$", s, re.I)
    if not m:
        return None
    acc_before = m.group(1) or ""
    note = m.group(2).upper()
    acc_after = m.group(3) or ""
    acc_str = acc_before + acc_after
    return (NOTE_TO_PC.get(note, 0) + acc_str.count("#") - acc_str.count("b")) % 12


def chord_symbol_to_notation(symbol: str, tonality_offset: int = 0) -> Optional[str]:
    """解析和弦符号，返回简谱记号字符串（如 "5 7 2. 4."）"""
    parts = parse_chord_symbol(symbol, tonality_offset)
    if not parts:
        return None
    return " ".join(parts)


def find_chord_symbol_tokens(
    content: str, start_pos: int = 0, end_pos: Optional[int] = None
) -> list[tuple[int, int, str]]:
    """
    在文本中查找 [V7]、[G7]、[vii°] 等和弦符号。
    返回 [(start, end, symbol), ...]，仅包含可解析的和弦符号。
    """
    if end_pos is None:
        end_pos = len(content)
    result = []
    for m in re.finditer(r"\[([^\[\]]+)\]", content):
        if m.start() >= end_pos or m.end() <= start_pos:
            continue
        inner = m.group(1).strip()
        if parse_chord_symbol(inner, 0):
            result.append((m.start(), m.end(), inner))
    return result


def expand_chord_symbols_in_text(text: str, tonality_offset: int = 0) -> str:
    """
    将文本中的 [V7]、[G7]、[vii°] 等和弦符号展开为简谱音级。
    保留非和弦符号的 [xxx]（如 [8va]）不变。
    """
    pattern = re.compile(r"\[([^\[\]]+)\]")
    result = []
    last_end = 0
    for m in pattern.finditer(text):
        result.append(text[last_end : m.start()])
        inner = m.group(1).strip()
        expanded = chord_symbol_to_notation(inner, tonality_offset)
        if expanded:
            result.append(expanded)
        else:
            result.append(m.group(0))
        last_end = m.end()
    result.append(text[last_end:])
    return "".join(result)
