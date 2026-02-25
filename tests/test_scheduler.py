"""
事件调度器回归测试
"""
import pytest

from src.core.parser import parse
from src.core.scheduler import schedule, schedule_segments, ScheduledNote, ScheduledSegment


def test_schedule_simple():
    """简单旋律调度"""
    text = r"""
\tonality{0}
\beat{4/4}
\bpm{120}

|1 2 3 4|5 6 7 1|
"""
    score = parse(text)
    notes = schedule(score)
    assert len(notes) >= 4  # 至少 4 个音符
    for n in notes:
        assert isinstance(n, ScheduledNote)
        assert n.start_time >= 0
        assert n.duration > 0
        assert len(n.midis) >= 1


def test_schedule_segments():
    """篇章调度（含 TTS 占位）"""
    text = r"""
\tonality{0}
\beat{4/4}
\bpm{120}

|1 2 3 4|

\tts{测试}{zh}

|5 6 7 1|
"""
    score = parse(text)
    segments = schedule_segments(score)
    assert len(segments) >= 1
    for seg in segments:
        assert isinstance(seg, ScheduledSegment)
        assert isinstance(seg.notes, list)


def test_schedule_multi_part():
    """多声部调度"""
    text = r"""
\tonality{0}
\beat{4/4}
\bpm{120}

& |1 2 3 4|
& |5 6 7 1|
"""
    score = parse(text)
    notes = schedule(score)
    assert len(notes) >= 4
