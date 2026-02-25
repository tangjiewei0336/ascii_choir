"""
和弦输入补全：根据已配置的和弦类型生成补全候选。
支持根音（C、Gm、F#aug）与级数（I、iv、V7），含转位（/E、/C）。
转位低音须为和弦音，排除 Caug/D 等不可能和弦。
可结合伴奏型：将 1、2、3、4 替换为和弦音，作为预览并插入 define。
"""
from pathlib import Path
from typing import Optional

from src.utils.chord_symbols import (
    parse_chord_symbol,
    get_chord_pitch_classes,
    get_chord_root_pc,
    parse_note_to_pc,
)

# 根音（含升降）
ROOTS = [
    "C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B",
]

# 级数（仅大写，小写与大写重复故不列）
ROMANS = ["I", "II", "III", "IV", "V", "VI", "VII"]
ROMANS_ACCIDENTAL = ["#I", "#IV", "#V", "bII", "bIII", "bVI", "bVII"]

# 和弦类型（根音用）与级数用省略形式；同一和弦多种写法均保留（如 Cdim、C°）
CHORD_TYPES_ROOT = ["maj", "m", "7", "maj7", "m7", "dim", "°", "aug", "+", "dim7", "°7", "m7b5"]
CHORD_TYPES_ROMAN = ["", "m", "7", "maj7", "m7", "dim", "°", "aug", "dim7", "°7", "m7b5"]

# 每个 pitch class 只保留一种拼写，避免 C#7 出现 C#7/F、C#7/Ab、C#7/Bb、C#7/G# 等多重转位
# 每和弦最多 3 个转位（3音、5音、7音）
BASS_BY_PC = {
    0: "C", 1: "C#", 2: "D", 3: "Eb", 4: "E", 5: "F",
    6: "F#", 7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B",
}


def _is_valid_slash_chord(symbol: str) -> bool:
    """转位和弦的低音须为和弦音，如 Caug/D 无效（D 不在 Caug 中）"""
    if "/" not in symbol:
        return True
    parts = symbol.split("/", 1)
    if len(parts) != 2:
        return True
    base_chord, bass = parts[0].strip(), parts[1].strip()
    chord_pcs = get_chord_pitch_classes(base_chord, 0)
    bass_pc = parse_note_to_pc(bass)
    if chord_pcs is None or bass_pc is None:
        return False
    return bass_pc in chord_pcs


def _build_chord_list() -> list[str]:
    """生成所有可补全的和弦符号（不含方括号）"""
    result: set[str] = set()

    # 根音 + 类型
    for root in ROOTS:
        for ct in CHORD_TYPES_ROOT:
            result.add(f"{root}{ct}")
        result.add(root)  # 大三可省略 maj

    # 级数 + 类型（小写 ii 默认 m，大写 I 默认 maj 可省略）
    for r in ROMANS + ROMANS_ACCIDENTAL:
        for ct in CHORD_TYPES_ROMAN:
            if ct == "m" and r[0].isupper():
                continue
            if ct == "" and r[0].islower():
                result.add(f"{r}m")  # 小写加 m
            elif ct == "" and r[0].isupper():
                result.add(r)
            else:
                result.add(f"{r}{ct}")

    # 转位：每和弦最多 3 个（3音、5音、7音），每个 pitch class 只保留一种拼写
    def _add_inversions(base: str) -> None:
        chord_pcs = get_chord_pitch_classes(base, 0)
        root_pc = get_chord_root_pc(base, 0)
        if not chord_pcs or root_pc is None:
            return
        for pc in chord_pcs:
            if pc == root_pc:
                continue  # 排除根音在 bass（根位）
            bass = BASS_BY_PC.get(pc)
            if bass:
                result.add(f"{base}/{bass}")

    for root in ROOTS:
        for ct in CHORD_TYPES_ROOT:
            base = f"{root}{ct}"
            _add_inversions(base)
        _add_inversions(root)

    # 级数转位
    for r in ROMANS + ROMANS_ACCIDENTAL:
        for ct in CHORD_TYPES_ROMAN:
            if ct == "m" and r[0].isupper():
                continue
            if ct == "" and r[0].islower():
                base = f"{r}m"
            elif ct == "" and r[0].isupper():
                base = r
            else:
                base = f"{r}{ct}"
            _add_inversions(base)

    return sorted(result, key=lambda s: s.lower())


_CHORD_LIST: list[str] | None = None


def _get_chord_list() -> list[str]:
    global _CHORD_LIST
    if _CHORD_LIST is None:
        _CHORD_LIST = _build_chord_list()
    return _CHORD_LIST


def _expand_chord_with_pattern(
    chord: str,
    tonality_offset: int,
    pattern_str: str,
    acc_tonality: int,
) -> str | None:
    """根据单个伴奏型将和弦展开为简谱音序列。"""
    from src.utils.accompaniment import (
        chord_parts_to_sorted_notation,
        expand_pattern_with_chord,
    )

    parts = parse_chord_symbol(chord, tonality_offset + acc_tonality)
    if not parts or len(parts) < 2 or len(parts) > 4:
        return None
    total_tonality = tonality_offset + acc_tonality
    sorted_parts = chord_parts_to_sorted_notation(parts, total_tonality)
    return expand_pattern_with_chord(pattern_str, sorted_parts)


def get_chord_suggestions(
    prefix: str,
    limit: int = 50,
    tonality_offset: int = 0,
    workspace_root: Optional[Path] = None,
    current_filename: Optional[str] = None,
    base_dir: Optional[Path] = None,
    insert_as_define: bool = False,
) -> list[tuple[str, str, str | None, int | None]]:
    """
    根据前缀返回和弦补全候选。
    返回 [(display, insert, notes_str, cursor_offset), ...]。
    若提供 workspace_root、current_filename，则从伴奏配置加载伴奏型，notes_str 为展开后的音序列。
    若 insert_as_define=True，insert 为 \\define{chord}{展开内容} 格式。
    """
    prefix = prefix.strip()
    if not prefix or not prefix[0].isalpha():
        return []

    patterns_3: list[str] = []
    patterns_4: list[str] = []
    acc_tonality = 0
    if workspace_root and workspace_root.is_dir() and current_filename:
        from src.utils.accompaniment import load_accompaniment

        cfg = load_accompaniment(workspace_root, current_filename)
        patterns_3 = cfg.get("patterns_3") or []
        patterns_4 = cfg.get("patterns_4") or []
        if not isinstance(patterns_3, list):
            patterns_3 = [patterns_3] if patterns_3 else []
        if not isinstance(patterns_4, list):
            patterns_4 = [patterns_4] if patterns_4 else []
        try:
            from src.core.parser import _tonality_to_semitones

            acc_tonality = _tonality_to_semitones(
                str(cfg.get("tonality", "0")).strip() or "0"
            )
        except Exception:
            acc_tonality = 0

    pl = prefix.lower().replace("°", "o")
    result: list[tuple[str, str, str | None, int | None]] = []
    for chord in _get_chord_list():
        chord_norm = chord.lower().replace("°", "o")
        if not chord_norm.startswith(pl):
            continue
        notes = parse_chord_symbol(chord, tonality_offset)
        pat_list = patterns_4 if notes and len(notes) == 4 else patterns_3
        if pat_list and len(pat_list) >= 2:
            # 多种伴奏型：每种生成一个条目，输入相同，提示内容不同
            for pattern_str in pat_list:
                notes_str = _expand_chord_with_pattern(
                    chord, tonality_offset, pattern_str, acc_tonality
                )
                if notes_str is None:
                    notes_str = " ".join(notes) if notes else None
                if insert_as_define and notes_str:
                    insert_val = f"\\define{{{chord}}}{{{notes_str}}}"
                    cursor_offset = 0
                else:
                    insert_val = f"[{chord}]"
                    cursor_offset = None
                result.append((chord, insert_val, notes_str, cursor_offset))
        else:
            # 无伴奏型或仅一种：单一条目
            notes_str: str | None = None
            if pat_list:
                notes_str = _expand_chord_with_pattern(
                    chord, tonality_offset, pat_list[0], acc_tonality
                )
            if notes_str is None and notes:
                notes_str = " ".join(notes)
            if insert_as_define and notes_str:
                insert_val = f"\\define{{{chord}}}{{{notes_str}}}"
                cursor_offset = 0
            else:
                insert_val = f"[{chord}]"
                cursor_offset = None
            result.append((chord, insert_val, notes_str, cursor_offset))
    # 未输入斜杠时，转位和弦（含 /）排在最后
    if "/" not in prefix:
        result.sort(key=lambda x: (("/" in x[0], x[0].lower())))
    if len(result) > limit:
        result = result[:limit]
    return result
