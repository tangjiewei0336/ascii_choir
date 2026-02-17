"""
事件调度器：将解析结果转为时间轴上的音符事件，支持多声部同步
"""
from dataclasses import dataclass, field
from parser import ParsedScore, NoteEvent, ChordEvent, RestEvent, BarContent, Part, TTSEvent


@dataclass
class ScheduledSegment:
    """调度后的篇章：该篇章前的 TTS + 音符列表（notes 的 start_time 相对于本篇章开始）"""
    tts_before: list[TTSEvent]
    notes: list["ScheduledNote"]


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
    """兼容接口：返回扁平化的音符列表（不含 TTS，供无 TTS 的简单播放）"""
    segments = schedule_segments(score)
    all_notes: list[ScheduledNote] = []
    t_offset = 0.0
    for seg in segments:
        for n in seg.notes:
            all_notes.append(ScheduledNote(
                start_time=n.start_time + t_offset,
                duration=n.duration,
                midis=n.midis,
                volume=n.volume,
            ))
        if seg.notes:
            t_offset += max(n.start_time + n.duration for n in seg.notes)
    all_notes.sort(key=lambda n: (n.start_time, -len(n.midis)))
    return all_notes


def schedule_segments(score: ParsedScore) -> list[ScheduledSegment]:
    """
    将 ParsedScore 转为篇章列表，每篇章含 TTS（前）和音符。
    支持 [dc] Da Capo 和 [fine] 结束记号。
    """
    segments: list[ScheduledSegment] = []
    section_tts = getattr(score, "section_tts", None) or []

    sections = getattr(score, "sections", None) or ([score.parts] if score.parts else [])
    section_settings = getattr(score, "section_settings", None) or []
    global_beat = 0.0

    for sec_idx, section in enumerate(sections):
        if not section:
            continue
        tts_before = section_tts[sec_idx] if sec_idx < len(section_tts) else []
        if sec_idx < len(section_settings):
            s = section_settings[sec_idx]
            bpm = s.bpm
            default_beats_per_bar = 4.0 if s.no_bar_check else float(s.beat_numerator)
        else:
            bpm = score.settings.bpm
            default_beats_per_bar = 4.0 if score.settings.no_bar_check else float(score.settings.beat_numerator)

        beats_per_second = bpm / 60.0
        aligned = _align_parts(section)
        notes1, next_beat, hit_fine, hit_dc = _collect_notes_from_aligned(
            aligned, global_beat, beats_per_second, default_beats_per_bar,
            stop_at_fine=False, stop_at_dc=True,
        )
        # 音符的 start_time 转为相对于本篇章开始（0）
        seg_start = global_beat / beats_per_second
        rel_notes = [
            ScheduledNote(n.start_time - seg_start, n.duration, n.midis, n.volume)
            for n in notes1
        ]
        segments.append(ScheduledSegment(tts_before=tts_before, notes=rel_notes))
        global_beat = next_beat

        if hit_dc:
            dc_beat = 0.0
            dc_segments: list[ScheduledSegment] = []
            for s_idx in range(sec_idx + 1):
                sec = sections[s_idx]
                tts = section_tts[s_idx] if s_idx < len(section_tts) else []
                if s_idx < len(section_settings):
                    ss = section_settings[s_idx]
                    bps = ss.bpm / 60.0
                    def_beats = 4.0 if ss.no_bar_check else float(ss.beat_numerator)
                else:
                    bps = score.settings.bpm / 60.0
                    def_beats = 4.0 if score.settings.no_bar_check else float(score.settings.beat_numerator)
                aligned = _align_parts(sec)
                nd, next_b, hit_f, _ = _collect_notes_from_aligned(
                    aligned, dc_beat, bps, def_beats, stop_at_fine=True
                )
                seg_s = dc_beat / bps
                rel = [ScheduledNote(n.start_time - seg_s, n.duration, n.midis, n.volume) for n in nd]
                dc_segments.append(ScheduledSegment(tts_before=tts, notes=rel))
                dc_beat = next_b
                if hit_f:
                    break
            segments.extend(dc_segments)
            break
        if hit_fine:
            break

    # 末尾 TTS（最后一篇章之后）
    if len(section_tts) > len(sections):
        trailing = section_tts[len(sections)]
        if trailing:
            segments.append(ScheduledSegment(tts_before=trailing, notes=[]))

    return segments
