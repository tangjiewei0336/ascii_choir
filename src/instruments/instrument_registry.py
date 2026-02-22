"""
乐器注册表：扫描 sound_library，提供音域、可弹判断、吉他选弦等。
"""
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

# 音色库根目录
SOUND_LIBRARY = Path(__file__).parent.parent.parent / "sound_library"
WAV_PATTERN = re.compile(r"German Concert D (\d{3}) 083\.wav", re.I)

# 吉他四根弦及其音域（按弦从粗到细：a, d, g, b）
GUITAR_STRINGS = ("a", "d", "g", "b")


def _scan_instrument_range(lib_path: Path) -> tuple[int, int]:
    """扫描目录中的 WAV，返回 (min_midi, max_midi)。无文件则返回 (0, 0)"""
    if not lib_path.exists() or not lib_path.is_dir():
        return 0, 0
    midis: list[int] = []
    for f in lib_path.iterdir():
        if f.is_file() and f.suffix.lower() == ".wav":
            m = WAV_PATTERN.match(f.name)
            if m:
                midis.append(int(m.group(1)))
    if not midis:
        return 0, 0
    return min(midis), max(midis)


@lru_cache(maxsize=1)
def get_all_instruments() -> dict[str, dict]:
    """
    扫描 sound_library，返回所有乐器及其音域。
    返回格式: { "乐器名": { "min_midi": int, "max_midi": int, "path": str, "is_guitar_string": bool } }
    结果已缓存，避免重复扫描文件系统（validate/诊断等高频调用时显著提速）。
    """
    result: dict[str, dict] = {}
    if not SOUND_LIBRARY.exists():
        return result

    for item in sorted(SOUND_LIBRARY.iterdir()):
        if not item.is_dir():
            continue
        name = item.name
        # 跳过 build 脚本输出目录、原始素材等
        if name in ("guitar_raw", "Musical Singal Notes") or name.startswith("."):
            continue
        # 跳过鼓声库原始目录（需先运行 build_drums.py 生成 drums）
        if name.startswith("16665__"):
            continue
        if name.startswith("guitar_string_"):
            # 吉他弦：单独记录，同时汇总到 guitar
            string_id = name.replace("guitar_string_", "")
            if string_id in GUITAR_STRINGS:
                lo, hi = _scan_instrument_range(item)
                if lo > 0 or hi > 0:
                    result[name] = {
                        "min_midi": lo,
                        "max_midi": hi,
                        "path": str(item.absolute()),
                        "is_guitar_string": True,
                        "string_id": string_id,
                    }
        else:
            lo, hi = _scan_instrument_range(item)
            if lo > 0 or hi > 0:
                result[name] = {
                    "min_midi": lo,
                    "max_midi": hi,
                    "path": str(item.absolute()),
                    "is_guitar_string": False,
                }

    # 汇总 guitar：四根弦的并集
    guitar_midis: list[int] = []
    for s in GUITAR_STRINGS:
        key = f"guitar_string_{s}"
        if key in result:
            guitar_midis.extend(
                range(result[key]["min_midi"], result[key]["max_midi"] + 1)
            )
    if guitar_midis:
        result["guitar"] = {
            "min_midi": min(guitar_midis),
            "max_midi": max(guitar_midis),
            "path": str(SOUND_LIBRARY / "guitar_string_g"),  # 默认路径，实际由选弦决定
            "is_guitar_string": False,
            "strings": list(GUITAR_STRINGS),
        }

    return result


def invalidate_instruments_cache() -> None:
    """清除乐器缓存（如 sound_library 有更新时调用）"""
    get_all_instruments.cache_clear()


def can_play_note(instrument: str, midi: int) -> bool:
    """
    判断给定乐器能否弹奏单个音符。
    instrument: 乐器名（如 "violin", "guitar", "guitar_string_a"）
    midi: MIDI 音符号 (21-108)
    返回: 若该乐器音域包含 midi 则 True
    """
    instruments = get_all_instruments()
    if instrument == "guitar":
        # 吉他：任一根弦能弹即可
        for s in GUITAR_STRINGS:
            key = f"guitar_string_{s}"
            if key in instruments:
                info = instruments[key]
                if info["min_midi"] <= midi <= info["max_midi"]:
                    return True
        return False
    if instrument.startswith("guitar_string_"):
        if instrument in instruments:
            info = instruments[instrument]
            return info["min_midi"] <= midi <= info["max_midi"]
        return False
    if instrument in instruments:
        info = instruments[instrument]
        return info["min_midi"] <= midi <= info["max_midi"]
    return False


def can_play_chord(instrument: str, midi_list: list[int]) -> bool:
    """
    判断给定乐器能否弹奏和弦（多个同时音符）。
    普通乐器：每个音都在音域内即可。
    吉他：每根弦同时只能弹一个音，需能分配每音到某弦且不冲突。
    guitar_string_*：单弦只能弹单音，多音则每个都需在该弦音域内（实际只能同时弹一个）。
    """
    if not midi_list:
        return True
    instruments = get_all_instruments()

    if instrument == "guitar":
        return select_guitar_strings_for_chord(midi_list) is not None

    if instrument.startswith("guitar_string_"):
        # 单弦：只能弹一个音，或多音同音（重复）
        if instrument in instruments:
            info = instruments[instrument]
            lo, hi = info["min_midi"], info["max_midi"]
            return len(midi_list) == 1 and lo <= midi_list[0] <= hi
        return False

    if instrument in instruments:
        info = instruments[instrument]
        lo, hi = info["min_midi"], info["max_midi"]
        return all(lo <= m <= hi for m in midi_list)
    return False


def select_guitar_strings_for_chord(midi_list: list[int]) -> Optional[list[tuple[int, str]]]:
    """
    为和弦中的每个音符自动选择吉他弦。
    每根弦同时只能弹一个音；优先用较低弦弹较低音（更自然的把位）。
    返回: [(midi, string_id), ...] 或 None（若无法分配）
    """
    instruments = get_all_instruments()
    # 按 MIDI 升序，低音优先分配
    sorted_midis = sorted(set(midi_list))
    # 每根弦的音域
    string_ranges: dict[str, tuple[int, int]] = {}
    for s in GUITAR_STRINGS:
        key = f"guitar_string_{s}"
        if key in instruments:
            info = instruments[key]
            string_ranges[s] = (info["min_midi"], info["max_midi"])

    result: list[tuple[int, str]] = []
    used_strings: set[str] = set()

    for midi in sorted_midis:
        assigned = False
        # 从低到高尝试弦（a 最低，b 最高）
        for s in GUITAR_STRINGS:
            if s in used_strings:
                continue
            if s not in string_ranges:
                continue
            lo, hi = string_ranges[s]
            if lo <= midi <= hi:
                result.append((midi, s))
                used_strings.add(s)
                assigned = True
                break
        if not assigned:
            return None

    return result


def select_guitar_string_for_note(midi: int) -> Optional[str]:
    """
    为单音选择吉他弦。优先选能弹该音的最低弦（音色更饱满）。
    """
    instruments = get_all_instruments()
    for s in GUITAR_STRINGS:
        key = f"guitar_string_{s}"
        if key in instruments:
            info = instruments[key]
            if info["min_midi"] <= midi <= info["max_midi"]:
                return s
    return None


def get_instrument_path_for_note(instrument: str, midi: int, chord_midis: Optional[list[int]] = None) -> Optional[str]:
    """
    获取播放某音符时应使用的音色库路径。
    普通乐器：直接返回乐器路径。
    吉他：根据 midi 和 chord_midis 选弦，返回对应 guitar_string_* 路径。
    """
    instruments = get_all_instruments()
    if instrument == "guitar":
        if chord_midis and len(chord_midis) > 1:
            assignment = select_guitar_strings_for_chord(chord_midis)
            if assignment:
                for m, sid in assignment:
                    if m == midi:
                        key = f"guitar_string_{sid}"
                        if key in instruments:
                            return instruments[key]["path"]
                        break
        else:
            sid = select_guitar_string_for_note(midi)
            if sid:
                key = f"guitar_string_{sid}"
                if key in instruments:
                    return instruments[key]["path"]
        return None
    if instrument in instruments:
        return instruments[instrument]["path"]
    return None


def midi_to_note_name(midi: int) -> str:
    """MIDI 转音符名，如 60 -> C4"""
    names = "C C# D D# E F F# G G# A A# B".split()
    octave = midi // 12 - 1
    note = names[midi % 12]
    return f"{note}{octave}"


# 简谱级数 1-7 对应 C D E F G A B 的 pitch class
_PC_TO_DEGREE = {0: 1, 1: 1, 2: 2, 3: 2, 4: 3, 5: 4, 6: 4, 7: 5, 8: 5, 9: 6, 10: 6, 11: 7}


def midi_to_simplified_notation(midi: int, tonality_offset: int = 0) -> str:
    """
    MIDI 转简谱格式，如 36 -> ..1，48 -> .1，60 -> 1。
    用于鼓声部等，输出 .1 .2 .3 这类纯数字格式，便于对齐。
    """
    base = midi - tonality_offset
    pc = base % 12
    octave = base // 12
    # C4=60 对应 octave 5，简谱 1 无点；每低一档 octave 加一个下点
    ref_octave = 5  # 1 无点
    octave_diff = ref_octave - octave
    degree = _PC_TO_DEGREE.get(pc, 1)
    acc = "#" if pc in (1, 3, 6, 8, 10) else ""
    dots = "." * max(0, octave_diff)
    return f"{dots}{acc}{degree}"


def note_name_to_midi(s: str) -> Optional[int]:
    """
    音名转 MIDI。支持格式：C4, C#4, Cb4, Db4, G#3 等。
    升号用 #，降号用 b。返回 None 表示解析失败。
    """
    s = s.strip()
    if not s:
        return None
    # 纯数字视为 MIDI
    if s.isdigit():
        v = int(s)
        return v if 21 <= v <= 108 else None
    # 解析音名：字母 + 可选升降 + 八度
    m = re.match(r"^([A-Ga-g])([#b]?)(-?\d+)\s*$", s)
    if not m:
        return None
    letter, acc, oct_str = m.group(1).upper(), m.group(2), m.group(3)
    octave = int(oct_str)
    # C=0, D=2, E=4, F=5, G=7, A=9, B=11
    base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[letter]
    if acc == "#":
        base += 1
    elif acc == "b":
        base -= 1
    base = base % 12
    # MIDI: C4=60, 即 (4+1)*12 + 0 = 60
    midi = (octave + 1) * 12 + base
    return midi if 21 <= midi <= 108 else None


def parse_note_or_chord_input(raw: str) -> Optional[list[int]]:
    """
    解析试听/可弹判断的输入。支持 MIDI 或音名，多音用 / 分隔。
    如：60、C4、60/64/67、C4/E4/G4
    """
    if not raw or not raw.strip():
        return None
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    result: list[int] = []
    for p in parts:
        midi = note_name_to_midi(p)
        if midi is None:
            return None
        result.append(midi)
    return result
