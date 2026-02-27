"""
简谱解析器回归测试
"""
import pytest

from src.core.parser import parse, ParseError, ParsedScore


def test_parse_simple_single_part():
    """单声部简单旋律"""
    text = r"""
\tonality{0}
\beat{4/4}
\bpm{120}

|1 2 3 4|5 6 7 1.|
"""
    score = parse(text)
    assert score is not None
    assert len(score.parts) >= 1
    assert score.settings.bpm == 120
    assert score.settings.beat_numerator == 4
    assert score.settings.beat_denominator == 4


def test_parse_tonality():
    """调性解析"""
    text = r"""
\tonality{C}
\beat{4/4}
\bpm{60}
|1 2 3|
"""
    score = parse(text)
    assert score is not None
    assert score.settings.tonality == "C"


def test_parse_chord():
    """和弦解析"""
    text = r"""
\tonality{0}
\beat{4/4}
\bpm{120}
|1/3/5 2 3|
"""
    score = parse(text)
    assert score is not None
    assert len(score.parts) >= 1
    bar = score.parts[0].bars[0]
    assert len(bar.events) >= 1
    ev = bar.events[0]
    assert hasattr(ev, "midis") or hasattr(ev, "midi")
    if hasattr(ev, "midis"):
        assert len(ev.midis) == 3


def test_parse_multi_part():
    """多声部"""
    text = r"""
\tonality{0}
\beat{4/4}
\bpm{120}

& |1 2 3 4|
& |5 6 7 1|
"""
    score = parse(text)
    assert score is not None
    assert len(score.parts) == 2


def test_parse_rest():
    """休止符"""
    text = r"""
\tonality{0}
\beat{4/4}
\bpm{120}
|0 1 2 0|
"""
    score = parse(text)
    assert score is not None


def test_parse_tie():
    """连音线"""
    text = r"""
\tonality{0}
\beat{4/4}
\bpm{120}
|1 ~1 2 3|
"""
    score = parse(text)
    assert score is not None


def test_parse_define():
    r"""\define 宏"""
    text = r"""
\define{foo}{1 2 3}
\tonality{0}
\beat{4/4}
\bpm{120}
|[foo] 4 5|
"""
    score = parse(text)
    assert score is not None


def test_parse_no_bar_check():
    """无小节校验"""
    text = r"""
\tonality{0}
\beat{4/4}
\no_bar_check
\bpm{120}
|1 2 3|
"""
    score = parse(text)
    assert score is not None
    assert score.settings.no_bar_check is True
