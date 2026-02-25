"""
预处理（import 展开）回归测试
"""
import tempfile
from pathlib import Path

import pytest

from src.core.preprocessor import expand_imports


def test_expand_imports_no_import():
    """无 import 时原样返回"""
    content = r"""
\tonality{0}
|1 2 3 4|
"""
    result = expand_imports(content, None)
    assert result == content


def test_expand_imports_with_base_dir(tmp_path):
    """有 base_dir 时展开 import"""
    sub = tmp_path / "sub.choir"
    sub.write_text("|5 6 7 1|", encoding="utf-8")
    content = r"""
\tonality{0}
\import{sub.choir}
"""
    result = expand_imports(content, tmp_path)
    assert "|5 6 7 1|" in result


def test_expand_imports_circular(tmp_path):
    """循环导入应抛出"""
    a = tmp_path / "a.choir"
    b = tmp_path / "b.choir"
    a.write_text(r"\import{b.choir}", encoding="utf-8")
    b.write_text(r"\import{a.choir}", encoding="utf-8")
    content = r"\import{a.choir}"
    with pytest.raises(ValueError, match="循环导入"):
        expand_imports(content, tmp_path)


def test_expand_imports_file_not_found(tmp_path):
    """导入不存在文件应抛出"""
    content = r"\import{nonexistent.choir}"
    with pytest.raises(FileNotFoundError, match="不存在"):
        expand_imports(content, tmp_path)
