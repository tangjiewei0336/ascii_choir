"""
和弦工具：在文本中查找和弦、交换两音、按音高排序。
"""
import re
from typing import Optional

from src.core.parser import parse_note_part_to_midi, _tonality_to_semitones


def _get_tonality_offset(content: str) -> int:
    """从内容开头解析 \\tonality，返回半音偏移。默认 C 大调=0"""
    m = re.search(r"\\tonality\{([^}]+)\}", content[:500], re.I)
    if m:
        return _tonality_to_semitones(m.group(1).strip())
    return 0


def _find_chord_tokens(content: str) -> list[tuple[int, int, str]]:
    """按与 parser 相同的 token 边界扫描，返回所有和弦 token 的 (start, end, text)"""
    result = []
    n = len(content)
    i = 0
    while i < n:
        if content[i] in " \t\n|[]()":
            i += 1
            continue
        j = i
        while j < n and content[j] not in " \t\n|[]()":
            j += 1
        tok = content[i:j]
        if "/" in tok and "{" not in tok and "}" not in tok:
            parts = [p.strip() for p in tok.rstrip("_").split("/") if p.strip()]
            if len(parts) >= 2 and all(
                any(c.isdigit() for c in p.lstrip("~.#b^")) and "0" != p.strip().lstrip("~.#b^")[:1]
                for p in parts
            ):
                result.append((i, j, tok))
        i = j
    return result


def find_chord_at_pos(content: str, char_pos: int) -> Optional[tuple[int, int, str]]:
    """若 char_pos 位于某和弦内，返回 (start, end, chord_text)，否则 None"""
    for start, end, text in _find_chord_tokens(content):
        if start <= char_pos < end:
            return (start, end, text)
    return None


def find_chords_in_range(
    content: str, start_pos: int, end_pos: int
) -> list[tuple[int, int, str]]:
    """返回与 [start_pos, end_pos) 有交集的全部和弦"""
    result = []
    for start, end, text in _find_chord_tokens(content):
        if start < end_pos and end > start_pos:
            result.append((start, end, text))
    return result


def _chord_parts(chord_text: str) -> tuple[list[str], str]:
    """拆分和弦为音符部分列表和尾部（如 _）。返回 (parts, suffix)"""
    chord_text = chord_text.strip()
    suffix = ""
    if chord_text.endswith("_"):
        suffix = "_"
        chord_text = chord_text[:-1]
    parts = [p.strip() for p in chord_text.split("/") if p.strip()]
    return parts, suffix


def chord_swap_two(chord_text: str) -> Optional[str]:
    """交换和弦中前两个音的顺序。仅当恰好两音时有效。如 1/3 -> 3/1"""
    parts, suffix = _chord_parts(chord_text)
    if len(parts) != 2:
        return None
    return parts[1] + "/" + parts[0] + suffix


def chord_sort(
    chord_text: str, ascending: bool = True, tonality_offset: int = 0
) -> str:
    """按音高排序和弦内各音。ascending=True 低到高，False 高到低"""
    parts, suffix = _chord_parts(chord_text)
    if len(parts) < 2:
        return chord_text
    octave = 4
    with_midi = [
        (p, parse_note_part_to_midi(p, octave, tonality_offset) or 0)
        for p in parts
    ]
    with_midi.sort(key=lambda x: x[1], reverse=not ascending)
    return "/".join(p for p, _ in with_midi) + suffix


def get_tonality_offset(content: str) -> int:
    """从内容解析调性偏移（供 chord_sort 使用）"""
    return _get_tonality_offset(content)


def _find_note_tokens(content: str) -> list[tuple[int, int, str]]:
    """按 token 边界扫描，返回所有音符/和弦 token 的 (start, end, text)。排除命令、休止等。"""
    result = []
    n = len(content)
    i = 0
    while i < n:
        if content[i] in " \t\n|[]()":
            i += 1
            continue
        j = i
        while j < n and content[j] not in " \t\n|[]()":
            j += 1
        tok = content[i:j]
        if "{" in tok or "}" in tok or tok.startswith("\\"):
            i = j
            continue
        if not tok or tok in ("-", "_"):
            i = j
            continue
        if any(c in "1234567" for c in tok):
            result.append((i, j, tok))
        i = j
    return result


def find_note_tokens_in_range(
    content: str, start_pos: int, end_pos: int
) -> list[tuple[int, int, str]]:
    """返回与 [start_pos, end_pos) 有交集的全部音符 token"""
    return [
        (s, e, t)
        for s, e, t in _find_note_tokens(content)
        if s < end_pos and e > start_pos
    ]


def duration_divide_two(tok: str) -> str | None:
    """时值除以2：在音符后加 _。四分->八分->十六分，最多十六分。"""
    trailing = 0
    for c in reversed(tok):
        if c == "_":
            trailing += 1
        else:
            break
    if trailing >= 2:
        return None
    return tok + "_"


def duration_multiply_two(tok: str) -> str | None:
    """时值乘以2：去掉一个 _。十六分->八分->四分，最多四分。"""
    if not tok.endswith("_"):
        return None
    return tok[:-1]


def get_chords_to_operate(
    content: str,
    sel_start: Optional[int],
    sel_end: Optional[int],
    cursor_pos: Optional[int],
) -> list[tuple[int, int, str]]:
    """
    根据选区或光标位置，返回要操作的和弦列表。
    - 有选区：返回选区内的所有和弦
    - 无选区：若光标在和弦内，返回该和弦
    - 否则返回空列表
    """
    if sel_start is not None and sel_end is not None and sel_start != sel_end:
        return find_chords_in_range(content, sel_start, sel_end)
    if cursor_pos is not None:
        ch = find_chord_at_pos(content, cursor_pos)
        if ch:
            return [ch]
    return []
