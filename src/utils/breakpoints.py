"""
断点存储：工作区隐藏文件 .ascii_choir_breakpoints.json
格式：{"文件名": [行号1, 行号2, ...], ...}
"""
import json
from pathlib import Path


BREAKPOINTS_FILENAME = ".ascii_choir_breakpoints.json"


def _breakpoints_path(base_dir: Path) -> Path:
    return base_dir / BREAKPOINTS_FILENAME


def load_breakpoints(base_dir: Path, filename: str) -> list[int]:
    """加载指定文件的断点行号列表，已排序。base_dir 为工作区根或文件所在目录"""
    p = _breakpoints_path(base_dir)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return []
        lines = data.get(filename, [])
        return sorted(set(int(x) for x in lines if isinstance(x, (int, float))))
    except (json.JSONDecodeError, OSError):
        return []


def save_breakpoints(base_dir: Path, filename: str, lines: list[int]) -> None:
    """保存指定文件的断点"""
    p = _breakpoints_path(base_dir)
    data: dict = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (json.JSONDecodeError, OSError):
            pass
    lines_sorted = sorted(set(lines))
    if lines_sorted:
        data[filename] = lines_sorted
    else:
        data.pop(filename, None)
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def rename_breakpoints(base_dir: Path, old_filename: str, new_filename: str) -> None:
    """重命名时同步断点：将 old_filename 的断点迁移到 new_filename"""
    p = _breakpoints_path(base_dir)
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
