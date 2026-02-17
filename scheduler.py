"""
事件调度器：将解析结果转为时间轴上的音符事件，支持多声部同步
"""
from dataclasses import dataclass, field
from parser import ParsedScore, NoteEvent, ChordEvent, RestEvent, BarContent, Part


@dataclass
class ScheduledNote:
    """调度后的音符：开始时间(秒)、时长(秒)、MIDI 列表、音量"""
    start_time: float
    duration: float
    midis: list[int]
    volume: float = 0.6


def _bar_events_to_scheduled(
    bar: BarContent,
    start_beat: float,
    beats_per_second: float,
) -> list[ScheduledNote]:
    """将小节内事件转为 (start_time, duration, midis) 列表"""
    result = []
    cursor = start_beat
    
    for ev in bar.events:
        if isinstance(ev, NoteEvent):
            result.append(ScheduledNote(
                start_time=cursor / beats_per_second,
                duration=ev.duration_beats / beats_per_second,
                midis=[ev.midi],
                volume=ev.volume,
            ))
            cursor += ev.duration_beats
        elif isinstance(ev, ChordEvent):
            result.append(ScheduledNote(
                start_time=cursor / beats_per_second,
                duration=ev.duration_beats / beats_per_second,
                midis=ev.midis.copy(),
                volume=ev.volume,
            ))
            cursor += ev.duration_beats
        elif isinstance(ev, RestEvent):
            cursor += ev.duration_beats
    
    return result


def _align_parts(parts: list[Part]) -> list[list[BarContent]]:
    """
    多声部对齐：按小节对齐，不足的声部用空小节填充。
    返回 aligned[bars][part_index]，即每个小节位置各声部的内容。
    """
    max_bars = max(len(p.bars) for p in parts) if parts else 0
    aligned: list[list[BarContent]] = []
    
    for bar_idx in range(max_bars):
        row = []
        for part in parts:
            if bar_idx < len(part.bars):
                row.append(part.bars[bar_idx])
            else:
                row.append(BarContent(events=[]))
        aligned.append(row)
    
    return aligned


def _get_bar_duration(bar: BarContent, default_beats: float) -> float:
    """计算小节总拍数"""
    total = 0.0
    for ev in bar.events:
        if isinstance(ev, NoteEvent):
            total += ev.duration_beats
        elif isinstance(ev, ChordEvent):
            total += ev.duration_beats
        elif isinstance(ev, RestEvent):
            total += ev.duration_beats
    return total if total > 0 else default_beats


def _collect_notes_from_aligned(
    aligned: list[list[BarContent]],
    start_beat: float,
    beats_per_second: float,
    default_beats_per_bar: float,
    stop_at_fine: bool = False,
    stop_at_dc: bool = False,
) -> tuple[list[ScheduledNote], float, bool, bool]:
    """
    从对齐的小节列表收集音符。
    返回 (notes, next_beat, hit_fine, hit_dc)
    """
    notes: list[ScheduledNote] = []
    global_beat = start_beat
    hit_fine = False
    hit_dc = False
    for bar_row in aligned:
        any_fine = any(getattr(bar, "fine", False) for bar in bar_row)
        any_dc = any(getattr(bar, "dc", False) for bar in bar_row)
        max_bar_beats = 0.0
        for bar in bar_row:
            d = _get_bar_duration(bar, default_beats_per_bar)
            max_bar_beats = max(max_bar_beats, d)
        for bar in bar_row:
            scheduled = _bar_events_to_scheduled(bar, global_beat, beats_per_second)
            notes.extend(scheduled)
        global_beat += max_bar_beats
        if any_fine:
            hit_fine = True
        if any_dc:
            hit_dc = True
        if stop_at_fine and hit_fine:
            break
        if stop_at_dc and hit_dc:
            break
    return notes, global_beat, hit_fine, hit_dc


def schedule(score: ParsedScore) -> list[ScheduledNote]:
    """
    将 ParsedScore 转为按时间排序的 ScheduledNote 列表。
    按篇章顺序渲染，每篇章内多声部对齐，篇章之间拼接。
    支持 [dc] Da Capo 和 [fine] 结束记号。
    """
    bpm = score.settings.bpm
    beats_per_second = bpm / 60.0
    
    if score.settings.no_bar_check:
        default_beats_per_bar = 4.0
    else:
        default_beats_per_bar = score.settings.beat_numerator
    
    all_notes: list[ScheduledNote] = []
    global_beat = 0.0
    
    sections = getattr(score, "sections", None) or ([score.parts] if score.parts else [])
    
    for section in sections:
        if not section:
            continue
        aligned = _align_parts(section)
        notes1, next_beat, hit_fine, hit_dc = _collect_notes_from_aligned(
            aligned, global_beat, beats_per_second, default_beats_per_bar,
            stop_at_fine=True, stop_at_dc=True,
        )
        all_notes.extend(notes1)
        global_beat = next_beat
        
        if hit_dc:
            notes_dc, _, _, _ = _collect_notes_from_aligned(
                aligned, 0.0, beats_per_second, default_beats_per_bar, stop_at_fine=True
            )
            offset = global_beat / beats_per_second
            for n in notes_dc:
                all_notes.append(ScheduledNote(
                    start_time=n.start_time + offset,
                    duration=n.duration,
                    midis=n.midis,
                    volume=n.volume,
                ))
            break
        if hit_fine:
            break
    
    all_notes.sort(key=lambda n: (n.start_time, -len(n.midis)))
    return all_notes
