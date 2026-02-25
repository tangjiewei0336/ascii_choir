"""
回归测试：使用 workspaces 中的实际简谱文件
"""
from pathlib import Path

import pytest

from src.core.parser import parse
from src.core.validator import validate
from src.core.scheduler import schedule
from src.core.preprocessor import expand_imports

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
WORKSPACES = ROOT / "workspaces"


def _collect_choir_files():
    """收集所有 .choir 文件路径"""
    if not WORKSPACES.is_dir():
        return []
    files = []
    for p in WORKSPACES.rglob("*.choir"):
        files.append(p)
    return sorted(files)


@pytest.mark.parametrize("choir_path", _collect_choir_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_workspace_choir_parse(choir_path):
    """workspaces 中每个 .choir 文件应能成功解析"""
    content = choir_path.read_text(encoding="utf-8")
    score = parse(content)
    assert score is not None


@pytest.mark.parametrize("choir_path", _collect_choir_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_workspace_choir_validate(choir_path):
    """workspaces 中每个 .choir 文件应能通过 validate（无解析错误）"""
    content = choir_path.read_text(encoding="utf-8")
    # 先展开 import（base_dir 为文件所在目录或工作区根）
    base_dir = choir_path.parent
    try:
        expanded = expand_imports(content, base_dir)
    except (FileNotFoundError, ValueError):
        pytest.skip(f"import 依赖缺失或循环: {choir_path}")
    score, diags = validate(expanded)
    errors = [d for d in diags if d.level == "error"]
    assert len(errors) == 0, f"{choir_path}: {errors}"


@pytest.mark.parametrize("choir_path", _collect_choir_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_workspace_choir_schedule(choir_path):
    """workspaces 中每个 .choir 文件应能成功调度"""
    content = choir_path.read_text(encoding="utf-8")
    base_dir = choir_path.parent
    try:
        expanded = expand_imports(content, base_dir)
    except (FileNotFoundError, ValueError):
        pytest.skip(f"import 依赖缺失或循环: {choir_path}")
    score = parse(expanded)
    assert score is not None
    notes = schedule(score)
    # 允许空（纯 TTS 等），但不应抛异常
    assert isinstance(notes, list)
