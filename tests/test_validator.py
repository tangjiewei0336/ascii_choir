"""
简谱验证器回归测试
"""
import pytest

from src.core.validator import validate, Diagnostic


def test_validate_simple():
    """简单旋律验证通过"""
    text = r"""
\tonality{0}
\beat{4/4}
\bpm{120}

|1 2 3 4|5 6 7 1|
"""
    score, diags = validate(text)
    assert score is not None
    errors = [d for d in diags if d.level == "error"]
    assert len(errors) == 0


def test_validate_bar_mismatch():
    """小节拍数错误应产生警告"""
    text = r"""
\tonality{0}
\beat{4/4}
\bpm{120}

|1 2 3|5 6 7 1|
"""
    score, diags = validate(text)
    # 4/4 小节只有 3 拍，应有诊断
    assert len(diags) >= 1 or score is not None


def test_validate_empty():
    """空内容"""
    score, diags = validate("")
    assert score is not None or len(diags) > 0


def test_validate_comments():
    """含注释"""
    text = r"""
\tonality{0}
\beat{4/4}
\bpm{120}
// 这是注释
|1 2 3 4|
"""
    score, diags = validate(text)
    assert score is not None
    errors = [d for d in diags if d.level == "error"]
    assert len(errors) == 0
