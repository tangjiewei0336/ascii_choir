"""
伴奏辅助：模式存储与解析
- 1音、2音、3音、4音 表示和弦中按音高从低到高的第 1/2/3/4 个音（含大三、小三、dim、aug 等）
- 时值记号：- 延长一拍，_ 减半，~ 连音线（与下一音连接）
- 存储于工作区隐藏文件 .ascii_choir_accompaniment.json
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


ACCOMPANIMENT_FILENAME = ".ascii_choir_accompaniment.json"


def _accompaniment_path(base_dir: Path) -> Path:
    return base_dir / ACCOMPANIMENT_FILENAME


def load_accompaniment(base_dir: Path, filename: str) -> dict:
    """
    加载指定文件的伴奏配置。
    返回 {
        "patterns_3": ["1 2 3", "1_ 2_ 3_"],
        "patterns_4": ["1 2 3 4"],
        "tonality": "0"  # 伴奏调性，0=与旋律同调，-12=低八度等
    } 或空 dict。兼容旧格式 pattern_3/pattern_4。
    """
    p = _accompaniment_path(base_dir)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        cfg = data.get(filename, {}) if isinstance(data.get(filename), dict) else {}
        # 兼容旧格式
        if "pattern_3" in cfg and "patterns_3" not in cfg:
            cfg["patterns_3"] = [cfg.pop("pattern_3", "1 2 3")]
        if "pattern_4" in cfg and "patterns_4" not in cfg:
            cfg["patterns_4"] = [cfg.pop("pattern_4", "1 2 3 4")]
        return cfg
    except (json.JSONDecodeError, OSError):
        return {}


def save_accompaniment(base_dir: Path, filename: str, config: dict) -> None:
    """保存指定文件的伴奏配置"""
    p = _accompaniment_path(base_dir)
    data: dict = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (json.JSONDecodeError, OSError):
            pass
    if config:
        data[filename] = config
    else:
        data.pop(filename, None)
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def rename_accompaniment(base_dir: Path, old_filename: str, new_filename: str) -> None:
    """重命名文件时迁移伴奏配置"""
    p = _accompaniment_path(base_dir)
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        if old_filename in data:
            data[new_filename] = data.pop(old_filename)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except (json.JSONDecodeError, OSError):
        pass


@dataclass
class PatternNote:
    """模式中的一个音符：和弦位置(0-3) 与 时值(拍数)"""
    position: int  # 0=最低音, 1=次低, 2=次高, 3=最高
    duration_beats: float
    tied_to_next: bool = False


def parse_accompaniment_pattern(pattern_str: str, base_duration: float = 1.0) -> list[PatternNote]:
    """
    解析伴奏模式字符串。
    例："1 2 3 4" -> 四个音各 1 拍
        "1- 2 3" -> 第1音 2 拍，第2音第3音各 1 拍
        "1_ 2_ 3_ 4_" -> 四个音各 0.5 拍
    """
    result: list[PatternNote] = []
    pattern_str = pattern_str.strip()
    if not pattern_str:
        return result
    tokens = re.split(r"\s+", pattern_str)
    for tok in tokens:
        tok = tok.strip()
        if not tok or tok in ("-", "_"):
            continue
        # 解析位置：1音 2音 3音 4音 或 1 2 3 4
        pos_match = re.match(r"^([1-4])", tok)
        if not pos_match:
            continue
        position = int(pos_match.group(1)) - 1  # 转为 0-based
        rest = tok[pos_match.end():]
        duration = base_duration
        tied = False
        for c in rest:
            if c == "-":
                duration += base_duration
            elif c == "_":
                duration /= 2
            elif c == "~":
                tied = True
        if duration > 0:
            result.append(PatternNote(position=position, duration_beats=duration, tied_to_next=tied))
    return result


def expand_pattern_with_chord(pattern_str: str, sorted_parts: list[str]) -> str:
    """
    将伴奏型中的 1、2、3、4 替换为和弦各音（简谱）。
    保留括号、空格、_、-、~ 等结构。
    """
    if not sorted_parts:
        return pattern_str
    mapping = {str(i + 1): sorted_parts[i] for i in range(min(4, len(sorted_parts)))}
    result: list[str] = []
    i = 0
    while i < len(pattern_str):
        c = pattern_str[i]
        if c in "1234" and (i + 1 >= len(pattern_str) or pattern_str[i + 1] not in "0123456789"):
            result.append(mapping.get(c, c))
            i += 1
        else:
            result.append(c)
            i += 1
    return "".join(result)


def chord_parts_to_sorted_notation(parts: list[str], tonality_offset: int = 0) -> list[str]:
    """
    将和弦各音（如 ["1","3","5"] 或 ["1","b3","#5"]）按音高排序，返回 notation 列表。
    1音=最低，2音=次低，3音=次高，4音=最高。适用于各类和弦（大三、小三、dim、aug 等）。
    """
    from src.core.parser import parse_note_part_to_midi
    octave = 4
    with_midi = [
        (p.strip(), parse_note_part_to_midi(p.strip(), octave, tonality_offset) or 0)
        for p in parts if p.strip()
    ]
    with_midi.sort(key=lambda x: x[1])
    return [p for p, _ in with_midi]
