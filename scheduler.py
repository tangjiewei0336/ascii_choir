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
    is_continuation: bool = False  # 连音延续，不重新触发，需与前一音合并
    instrument: str = "grand_piano"  # 音色，由 [cello][guitar] 等标记指定


def _merge_tied_events(
    events_with_start: list[tuple[float, "NoteEvent | ChordEvent | RestEvent"]],
) -> list[tuple[float, float, list[int], float, bool]]:
    """
    合并连音事件，返回 [(start_beat, duration, midis, volume, is_continuation), ...]。
    播放时连音为一个持续音，不重复触发。
    和弦内各音保持相同时值；连音延续单独输出并标记 is_continuation，由播放器合并。
    """
    result: list[tuple[float, float, list[int], float, bool]] = []
    i = 0
    while i < len(events_with_start):
        start_beat, ev = events_with_start[i]
        if isinstance(ev, RestEvent):
            i += 1
            continue
        if isinstance(ev, NoteEvent):
            dur = ev.duration_beats
            vol = ev.volume
            j = i + 1
            while getattr(ev, "tied_to_next", False) and j < len(events_with_start):
                nxt_start, nxt = events_with_start[j]
                if getattr(nxt, "tied_from_prev", False) and isinstance(nxt, NoteEvent) and nxt.midi == ev.midi:
                    dur += nxt.duration_beats
                    j += 1
                else:
                    break
            result.append((start_beat, dur, [ev.midi], vol, False))
            i = j
            continue
        if isinstance(ev, ChordEvent):
            vol = ev.volume
            merge_dur_by_midi: dict[int, float] = {m: 0.0 for m in ev.midis}
            extra_notes: list[tuple[float, float, int]] = []
            j = i + 1
            while getattr(ev, "tied_to_next", False) and j < len(events_with_start):
                nxt_start, nxt = events_with_start[j]
                if not getattr(nxt, "tied_from_prev", False):
                    break
                if isinstance(nxt, NoteEvent):
                    if nxt.midi in ev.midis:
                        merge_dur_by_midi[nxt.midi] += nxt.duration_beats
                        j += 1
                    else:
                        extra_notes.append((nxt_start, nxt.duration_beats, nxt.midi))
                        j += 1
                elif isinstance(nxt, ChordEvent):
                    tied_midis = getattr(nxt, "tied_from_prev_midis", []) or [
                        m for m in nxt.midis if m in ev.midis
                    ]
                    for m in ev.midis:
                        if m in tied_midis:
                            merge_dur_by_midi[m] += nxt.duration_beats
                    for m in nxt.midis:
                        if m not in ev.midis:
                            extra_notes.append((nxt_start, nxt.duration_beats, m))
                    j += 1
                    break
                else:
                    break
            # 琶音 [a]：从低到高快速连续，同时终止
            arpeggio = getattr(ev, "arpeggio", False) and len(ev.midis) > 1
            if arpeggio:
                delay_beats = 0.05  # 每音间隔约 50ms @ 120bpm
                sorted_midis = sorted(ev.midis)
                n = len(sorted_midis)
                for i, m in enumerate(sorted_midis):
                    note_start = start_beat + i * delay_beats
                    note_dur = ev.duration_beats - (n - 1 - i) * delay_beats
                    if note_dur > 0:
                        result.append((note_start, note_dur, [m], vol, False))
            else:
                # 和弦整体：各音相同时值
                result.append((start_beat, ev.duration_beats, list(ev.midis), vol, False))
            # 连音延续：仅被 tie 的音，从和弦结束后开始，标记 is_continuation
            for m in ev.midis:
                if merge_dur_by_midi[m] > 0:
                    result.append((start_beat + ev.duration_beats, merge_dur_by_midi[m], [m], vol, True))
            for nxt_start, nxt_dur, m in extra_notes:
                result.append((nxt_start, nxt_dur, [m], vol, False))
            i = j
            continue
        i += 1
    return result


def _part_events_to_scheduled(
    part_bars: list[BarContent],
    bar_starts: list[float],
    beats_per_second: float,
    part_index: int = 0,
    instrument: str = "grand_piano",
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
            is_continuation=is_cont,
            instrument=instrument,
        )
        for start, dur, midis, vol, is_cont in merged
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
    section_part_instruments: dict[int, str] | None = None,
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
        inst = (section_part_instruments or {}).get(part_idx, "grand_piano")
        part_notes = _part_events_to_scheduled(
            part_bars, bar_starts[:-1], beats_per_second, part_index=part_idx, instrument=inst
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
                is_continuation=getattr(n, "is_continuation", False),
                instrument=getattr(n, "instrument", "grand_piano"),
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
        section_part_instruments = (
            score.section_part_instruments[sec_idx]
            if hasattr(score, "section_part_instruments") and sec_idx < len(getattr(score, "section_part_instruments", []))
            else None
        )
        notes1, next_beat, hit_fine, hit_dc = _collect_notes_from_aligned(
            aligned, global_beat, beats_per_second, default_beats_per_bar,
            stop_at_fine=False, stop_at_dc=True,
            section_part_instruments=section_part_instruments,
        )
        # 音符的 start_time 转为相对于本篇章开始（0）
        seg_start = global_beat / beats_per_second
        rel_notes = [
            ScheduledNote(
                n.start_time - seg_start, n.duration, n.midis, n.volume, n.part_index,
                is_continuation=getattr(n, "is_continuation", False),
                instrument=getattr(n, "instrument", "grand_piano"),
            )
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
                sec_inst = score.section_part_instruments[s_idx] if hasattr(score, "section_part_instruments") and s_idx < len(getattr(score, "section_part_instruments", [])) else None
                nd, next_b, hit_f, _ = _collect_notes_from_aligned(
                    aligned, dc_beat, bps, def_beats, stop_at_fine=True,
                    section_part_instruments=sec_inst,
                )
                seg_s = dc_beat / bps
                rel = [
                    ScheduledNote(n.start_time - seg_s, n.duration, n.midis, n.volume, n.part_index,
                        is_continuation=getattr(n, "is_continuation", False),
                        instrument=getattr(n, "instrument", "grand_piano"),
                    )
                    for n in nd
                ]
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
