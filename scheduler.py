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
    section_index: int = 0  # 对应 score.section_lyrics 的索引


@dataclass
class ScheduledNote:
    """调度后的音符：开始时间(秒)、时长(秒)、MIDI 列表、音量"""
    start_time: float
    duration: float
    midis: list[int]
    volume: float = 0.6
    part_index: int = 0  # 声部索引，用于歌词合成时筛选旋律


def _merge_tied_events(
    events_with_start: list[tuple[float, "NoteEvent | ChordEvent | RestEvent"]],
) -> list[tuple[float, float, list[int], float]]:
    """
    合并连音事件，返回 [(start_beat, duration, midis, volume), ...]。
    播放时连音为一个持续音，不重复触发。
    支持跨小节、单音连音、和弦连音（按音高分别合并，如 1/3 ~ 3/5）。
    """
    result: list[tuple[float, float, list[int], float]] = []
    i = 0
    while i < len(events_with_start):
        start_beat, ev = events_with_start[i]
        if isinstance(ev, RestEvent):
            i += 1
            continue
        if isinstance(ev, NoteEvent):
            dur = ev.duration_beats
            vol = ev.volume
            if getattr(ev, "tied_to_next", False) and i + 1 < len(events_with_start):
                nxt_start, nxt = events_with_start[i + 1]
                if getattr(nxt, "tied_from_prev", False) and isinstance(nxt, NoteEvent) and nxt.midi == ev.midi:
                    dur += nxt.duration_beats
                    i += 1
            result.append((start_beat, dur, [ev.midi], vol))
            i += 1
            continue
        if isinstance(ev, ChordEvent):
            vol = ev.volume
            nxt_dur = 0.0
            nxt_midis: list[int] = []
            tied_midis: list[int] = []
            if getattr(ev, "tied_to_next", False) and i + 1 < len(events_with_start):
                _, nxt = events_with_start[i + 1]
                if getattr(nxt, "tied_from_prev", False):
                    tied_midis = getattr(nxt, "tied_from_prev_midis", [])
                    if isinstance(nxt, ChordEvent):
                        nxt_dur = nxt.duration_beats
                        nxt_midis = nxt.midis
                    elif isinstance(nxt, NoteEvent):
                        nxt_dur = nxt.duration_beats
                        nxt_midis = [nxt.midi]
            # 按音高分别输出：ev 中的音若在 nxt 的 tied_from_prev_midis 里则合并时长
            for m in ev.midis:
                d = ev.duration_beats
                if m in tied_midis:
                    d += nxt_dur
                result.append((start_beat, d, [m], vol))
            # nxt 中仅在后段出现的音（如 1/3 ~ 3/5 中的 5）
            for m in nxt_midis:
                if m not in ev.midis:
                    result.append((start_beat + ev.duration_beats, nxt_dur, [m], vol))
            if nxt_dur > 0:
                i += 1  # 已合并，跳过 nxt
            i += 1
            continue
        i += 1
    return result


def _part_events_to_scheduled(
    part_bars: list[BarContent],
    bar_starts: list[float],
    beats_per_second: float,
    part_index: int = 0,
) -> list[ScheduledNote]:
    """将一声部的所有小节事件转为 ScheduledNote，跨小节连音已合并"""
    events_with_start: list[tuple[float, NoteEvent | ChordEvent | RestEvent]] = []
    for bar_idx, bar in enumerate(part_bars):
        bar_start = bar_starts[bar_idx] if bar_idx < len(bar_starts) else 0.0
        cursor = 0.0
        for ev in bar.events:
            if isinstance(ev, (NoteEvent, ChordEvent, RestEvent)):
                events_with_start.append((bar_start + cursor, ev))
            if isinstance(ev, NoteEvent):
                cursor += ev.duration_beats
            elif isinstance(ev, ChordEvent):
                cursor += ev.duration_beats
            elif isinstance(ev, RestEvent):
                cursor += ev.duration_beats
    merged = _merge_tied_events(events_with_start)
    return [
        ScheduledNote(
            start_time=start / beats_per_second,
            duration=dur / beats_per_second,
            midis=midis,
            volume=vol,
            part_index=part_index,
        )
        for start, dur, midis, vol in merged
    ]


def _bar_events_to_scheduled(
    bar: BarContent,
    start_beat: float,
    beats_per_second: float,
) -> list[ScheduledNote]:
    """单小节事件转 ScheduledNote（用于兼容，跨小节连音需用 _part_events_to_scheduled）"""
    return _part_events_to_scheduled([bar], [start_beat], beats_per_second)


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
    从对齐的小节列表收集音符。按声部合并跨小节连音后调度。
    返回 (notes, next_beat, hit_fine, hit_dc)
    """
    # 先确定要处理到哪一小节（fine/dc 可能提前结束）
    bars_to_process: list[list[BarContent]] = []
    global_beat = start_beat
    hit_fine = False
    hit_dc = False
    for bar_row in aligned:
        any_fine = any(getattr(bar, "fine", False) for bar in bar_row)
        any_dc = any(getattr(bar, "dc", False) for bar in bar_row)
        max_bar_beats = max(
            _get_bar_duration(bar, default_beats_per_bar) for bar in bar_row
        )
        bars_to_process.append(bar_row)
        global_beat += max_bar_beats
        if any_fine:
            hit_fine = True
        if any_dc:
            hit_dc = True
        if stop_at_fine and hit_fine:
            break
        if stop_at_dc and hit_dc:
            break
    # 计算每小节的起始拍
    bar_starts: list[float] = [start_beat]
    for bar_row in bars_to_process:
        max_bar_beats = max(
            _get_bar_duration(bar, default_beats_per_bar) for bar in bar_row
        )
        bar_starts.append(bar_starts[-1] + max_bar_beats)
    # 按声部收集并合并连音
    num_parts = len(bars_to_process[0]) if bars_to_process else 0
    notes: list[ScheduledNote] = []
    for part_idx in range(num_parts):
        part_bars = [row[part_idx] for row in bars_to_process]
        part_notes = _part_events_to_scheduled(
            part_bars, bar_starts[:-1], beats_per_second, part_index=part_idx
        )
        notes.extend(part_notes)
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
                part_index=n.part_index,
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
            ScheduledNote(n.start_time - seg_start, n.duration, n.midis, n.volume, n.part_index)
            for n in notes1
        ]
        segments.append(ScheduledSegment(tts_before=tts_before, notes=rel_notes, section_index=sec_idx))
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
                rel = [ScheduledNote(n.start_time - seg_s, n.duration, n.midis, n.volume, n.part_index) for n in nd]
                dc_segments.append(ScheduledSegment(tts_before=tts, notes=rel, section_index=s_idx))
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
