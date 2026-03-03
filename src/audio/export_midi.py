"""
将简谱解析结果导出为 MIDI 文件。
依赖 mido 库。
"""
from pathlib import Path
from typing import Optional

# GM 乐器 -> MIDI Program (0-127)
INSTRUMENT_TO_PROGRAM: dict[str, int] = {
    "grand_piano": 0,
    "piano": 0,
    "violin": 40,
    "cello": 42,
    "trumpet": 56,
    "clarinet": 71,
    "oboe": 68,
    "alto_sax": 65,
    "tenor_sax": 66,
    "bass": 32,
    "guitar": 25,
    "guitar_electric": 27,
    "bass_electric": 33,
    "drums": 0,  # 鼓用 channel 9，program 忽略
}


def _volume_to_velocity(vol: float) -> int:
    """音量 0-1 转为 MIDI velocity 1-127"""
    return max(1, min(127, int(vol * 127)))


def _merge_continuation_notes(notes: list) -> list:
    """
    合并 is_continuation 音符到前一音符。
    返回 [(start_time, end_time, midis, velocity, instrument, part_index), ...]
    """
    result: list[tuple[float, float, list[int], int, str, int]] = []
    for n in notes:
        if getattr(n, "is_continuation", False) and len(n.midis) == 1:
            midi = n.midis[0]
            # 找前一音符：结束时间等于本音符开始，且包含该 midi
            merged = False
            for i in range(len(result) - 1, -1, -1):
                start, end, midis, vel, inst, part = result[i]
                if abs(end - n.start_time) < 1e-6 and midi in midis and part == n.part_index:
                    result[i] = (start, n.start_time + n.duration, midis, vel, inst, part)
                    merged = True
                    break
            if merged:
                continue
        # 非 continuation 或未找到前一音符
        vel = _volume_to_velocity(n.volume)
        inst = getattr(n, "instrument", "grand_piano")
        part = getattr(n, "part_index", 0)
        result.append((n.start_time, n.start_time + n.duration, list(n.midis), vel, inst, part))
    return result


def export_score_to_midi(
    score,
    path: str | Path,
    ticks_per_beat: int = 480,
) -> tuple[Optional[str], Optional[str]]:
    """
    将 ParsedScore 导出为 MIDI 文件。
    返回 (保存路径, 错误信息)。成功时错误为 None，失败时路径为 None。
    """
    try:
        from mido import MidiFile, MidiTrack, Message, MetaMessage, bpm2tempo
    except ImportError:
        return None, "mido 未安装，请运行: pip install mido"

    from src.core.scheduler import schedule

    path = Path(path)
    path = path.with_suffix(".mid")

    notes = schedule(score)
    if not notes:
        return None, "曲目为空，无法导出 MIDI"

    bpm = score.settings.bpm
    tempo = bpm2tempo(bpm)
    beats_per_second = bpm / 60.0
    seconds_to_ticks = lambda s: int(s * beats_per_second * ticks_per_beat)

    merged = _merge_continuation_notes(notes)
    # 按 part_index 分组，同 part 同 instrument 的放同一 track
    parts: dict[int, list] = {}
    for start, end, midis, vel, inst, part_idx in merged:
        if part_idx not in parts:
            parts[part_idx] = []
        parts[part_idx].append((start, end, midis, vel, inst))

    mid = MidiFile(type=1, ticks_per_beat=ticks_per_beat)

    # Track 0: 仅 tempo
    tempo_track = MidiTrack()
    tempo_track.append(MetaMessage("set_tempo", tempo=tempo, time=0))
    tempo_track.append(MetaMessage("end_of_track", time=0))
    mid.tracks.append(tempo_track)

    for part_idx in sorted(parts.keys()):
        part_notes = parts[part_idx]
        track = MidiTrack()
        # 取该 part 第一个音符的乐器
        inst = part_notes[0][4] if part_notes else "grand_piano"
        is_drums = inst == "drums"
        # 鼓固定 channel 9，其他 part 分配 0-8, 10-15
        if is_drums:
            channel = 9
        else:
            chs = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15]
            channel = chs[part_idx % len(chs)]
        program = 0 if is_drums else INSTRUMENT_TO_PROGRAM.get(inst, 0)
        track.append(Message("program_change", program=program, channel=channel, time=0))

        # 收集该 part 所有音符，按时间排序
        events: list[tuple[float, str, int, int]] = []  # (time, 'on'|'off', note, velocity)
        for start, end, midis, vel, _ in part_notes:
            for m in midis:
                if 0 <= m <= 127:
                    events.append((start, "on", m, vel))
                    events.append((end, "off", m, 0))
        events.sort(key=lambda x: (x[0], 0 if x[1] == "on" else 1))

        last_tick = 0
        for t, kind, note, vel in events:
            tick = seconds_to_ticks(t)
            delta = max(0, tick - last_tick)
            last_tick = tick
            if kind == "on":
                track.append(Message("note_on", note=note, velocity=vel, channel=channel, time=delta))
            else:
                track.append(Message("note_off", note=note, velocity=0, channel=channel, time=delta))

        track.append(MetaMessage("end_of_track", time=0))
        mid.tracks.append(track)

    mid.save(str(path))
    return str(path), None
