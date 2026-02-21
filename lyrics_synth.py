"""
Lyrics 歌声合成：将简谱音符与歌词转为 VOICEVOX 歌唱 API 格式并合成
通过哈希缓存已生成的歌声，相同篇章直接加载缓存。
"""
import io
import uuid
from typing import Optional

import numpy as np
import soundfile as sf

from audio_cache import cache_key_lyrics, get_cached_audio, save_audio_to_cache
from parser import ParsedScore, NoteEvent, ChordEvent, RestEvent, BarContent
from scheduler import _align_parts, _get_bar_duration
from voicevox_client import (
    sing_frame_audio_query,
    frame_synthesis,
    SING_FRAME_RATE,
    resolve_singing_style_id,
    VOICEVOX_BASE,
)


def get_lyrics_part_index(score: ParsedScore, section_index: int) -> Optional[int]:
    """获取指定篇章的歌词声部索引，无歌词则返回 None。多声部时返回第一个。"""
    indices = get_lyrics_part_indices(score, section_index)
    return indices[0] if indices else None


def get_lyrics_part_indices(score: ParsedScore, section_index: int) -> list[int]:
    """获取指定篇章所有带 voice_id 的歌词声部索引（人声轨）"""
    section_lyrics = score.section_lyrics or []
    if section_index >= len(section_lyrics):
        return []
    seen: set[int] = set()
    result: list[int] = []
    for part_idx, syllables, voice_id, _, _ in section_lyrics[section_index]:
        if voice_id is not None and syllables and part_idx not in seen:
            seen.add(part_idx)
            result.append(part_idx)
    return result


def has_lyrics_voice(score: ParsedScore, section_index: int) -> bool:
    """该篇章是否有带 voice_id 的 \\lyrics"""
    section_lyrics = score.section_lyrics or []
    if section_index >= len(section_lyrics):
        return False
    for _, syllables, voice_id, _, _ in section_lyrics[section_index]:
        if voice_id is not None and syllables:
            return True
    return False


def has_lyrics_syllables(score: ParsedScore, section_index: int) -> bool:
    """该篇章是否有 \\lyrics 音节（可配合 voice_id_override 使用）"""
    section_lyrics = score.section_lyrics or []
    if section_index >= len(section_lyrics):
        return False
    for _, syllables, _, _, _ in section_lyrics[section_index]:
        if syllables:
            return True
    return False


def _build_sing_notes_for_entry(
    score: ParsedScore,
    section_index: int,
    part_idx: int,
    voice_id: int,
    melody_part: int,
    part_queue: list[str],
    max_duration_seconds: Optional[float] = None,
) -> Optional[list[tuple[float, float, Optional[int], str]]]:
    """为单个 (part_idx, voice_id, melody_part) 构建歌唱音符列表。"""
    sections = score.sections or []
    section_settings = score.section_settings or []

    if section_index >= len(sections):
        return None

    result: list[tuple[float, float, Optional[int], str]] = []
    global_beat = 0.0
    queue = list(part_queue)

    for sec_idx in range(section_index, len(sections)):
        section = sections[sec_idx]
        if part_idx >= len(section):
            break
        if sec_idx < len(section_settings):
            s = section_settings[sec_idx]
        else:
            s = score.settings
        bpm = s.bpm
        default_beats = 4.0 if s.no_bar_check else float(s.beat_numerator)
        beats_per_second = bpm / 60.0

        aligned = _align_parts(section)
        bar_starts: list[float] = [global_beat]
        for bar_row in aligned:
            max_beats = max(_get_bar_duration(bar, default_beats) for bar in bar_row)
            bar_starts.append(bar_starts[-1] + max_beats)
        global_beat = bar_starts[-1]

        part_bars = [row[part_idx] for row in aligned]
        for bar_idx, bar in enumerate(part_bars):
            bar_start = bar_starts[bar_idx] if bar_idx < len(bar_starts) else 0.0
            cursor = 0.0
            for ev in bar.events:
                start_beat = bar_start + cursor
                if isinstance(ev, RestEvent):
                    st = start_beat / beats_per_second
                    dur = ev.duration_beats / beats_per_second
                    if max_duration_seconds is not None and st >= max_duration_seconds:
                        cursor += ev.duration_beats
                        continue
                    if max_duration_seconds is not None and st + dur > max_duration_seconds:
                        dur = max_duration_seconds - st
                    result.append((st, dur, None, ""))
                    cursor += ev.duration_beats
                    continue
                lyric = ""
                if getattr(ev, "lyric", None):
                    lyric = ev.lyric
                elif queue:
                    lyric = queue.pop(0)
                if isinstance(ev, NoteEvent):
                    st = start_beat / beats_per_second
                    dur = ev.duration_beats / beats_per_second
                    if max_duration_seconds is not None and st >= max_duration_seconds:
                        cursor += ev.duration_beats
                        continue
                    if max_duration_seconds is not None and st + dur > max_duration_seconds:
                        dur = max_duration_seconds - st
                    midi = ev.midi
                    if _is_hold_lyric(lyric) and result:
                        prev_st, prev_dur, prev_midi, prev_lyric = result[-1]
                        result[-1] = (prev_st, prev_dur + dur, prev_midi, prev_lyric)
                    else:
                        result.append((st, dur, midi, lyric))
                    cursor += ev.duration_beats
                elif isinstance(ev, ChordEvent):
                    st = start_beat / beats_per_second
                    dur = ev.duration_beats / beats_per_second
                    if max_duration_seconds is not None and st >= max_duration_seconds:
                        cursor += ev.duration_beats
                        continue
                    if max_duration_seconds is not None and st + dur > max_duration_seconds:
                        dur = max_duration_seconds - st
                    midi = ev.midis[melody_part] if melody_part < len(ev.midis) else ev.midis[0]
                    if _is_hold_lyric(lyric) and result:
                        prev_st, prev_dur, prev_midi, prev_lyric = result[-1]
                        result[-1] = (prev_st, prev_dur + dur, prev_midi, prev_lyric)
                    else:
                        result.append((st, dur, midi, lyric))
                    cursor += ev.duration_beats

    return result if result else None


def _build_sing_notes(
    score: ParsedScore,
    section_index: int,
    max_duration_seconds: Optional[float] = None,
    voice_id_override: Optional[int] = None,
) -> Optional[tuple[int, list[tuple[float, float, Optional[int], str]]]]:
    """
    从篇章构建歌唱用音符列表（不合并连音，保证音节与音符一一对应）。
    歌词可能跨多篇章，会从 section_index 起遍历后续所有篇章直到歌词耗尽。
    休止符 0 会插入为 (st, dur, None, "")。返回 (voice_id, [(start_time, duration, midi, lyric), ...]) 或 None
    多声部人声时仅返回第一个，实际合成由 _synthesize_section 处理全部。
    """
    sections = score.sections or []
    section_lyrics = score.section_lyrics or []
    section_settings = score.section_settings or []

    if section_index >= len(sections):
        return None
    sec_lyrics = section_lyrics[section_index] if section_index < len(section_lyrics) else []

    # 取第一个带 voice_id 或 syllables 的 lyrics 条目（有 override 时可用无 voice_id 的）
    entry = None
    for part_idx, syllables, voice_id, melody_part, _ in sec_lyrics:
        effective_id = voice_id_override if voice_id_override is not None else voice_id
        if effective_id is not None and syllables:
            entry = (part_idx, effective_id, melody_part)
            break
    if not entry:
        return None
    part_idx, voice_id, melody_part = entry

    # 该声部的音节队列（同一声部多个 \\lyrics 会合并）
    part_queue: list[str] = []
    for pidx, s, _, _, _ in sec_lyrics:
        if pidx == part_idx:
            part_queue.extend(s)

    result = _build_sing_notes_for_entry(
        score, section_index, part_idx, voice_id, melody_part, part_queue, max_duration_seconds
    )
    return (voice_id, result) if result else None


def _is_hold_lyric(lyric: str) -> bool:
    """是否为延长/休止音节（"-"、"ー" 等），应合并到前一个音"""
    if not lyric:
        return True
    s = lyric.strip()
    return s in ("-", "ー", "−", "―")


def _notes_to_voicevox_format(
    notes: list[tuple[float, float, Optional[int], str]],
) -> list[dict]:
    """将 (start_time, duration, midi, lyric) 转为 VOICEVOX /sing_frame_audio_query 的 notes 格式。hold 已合并到前音，休止符 midi=None。首个必须是空 note。"""
    out: list[dict] = [
        {"id": str(uuid.uuid4()), "key": None, "frame_length": 2, "lyric": ""},
    ]
    for start_time, duration, midi, lyric in notes:
        if _is_hold_lyric(lyric) and midi is not None:
            continue  # 理论上已合并，防御性跳过；休止符 midi=None 不跳过
        frame_length = int(round(duration * SING_FRAME_RATE))
        if frame_length < 1:
            frame_length = 1
        out.append({
            "id": str(uuid.uuid4()),
            "key": midi,  # 休止符为 None
            "frame_length": frame_length,
            "lyric": lyric.strip() if lyric else "",  # 休止符 lyric 为空
        })
    return out


def _synthesize_one_voice(
    notes: list[tuple[float, float, Optional[int], str]],
    voice_id: int,
    sample_rate: int,
    base_url: str,
) -> Optional[np.ndarray]:
    """合成单个人声轨，返回 float32 数组或 None"""
    vv_notes = _notes_to_voicevox_format(notes)
    if not vv_notes:
        return None
    sing_id = resolve_singing_style_id(voice_id, base_url)
    if sing_id is None:
        raise RuntimeError(
            "未找到歌唱用角色。请安装支持歌唱的音声库（如波音リツ），或确认 voicevox_engine 已加载歌唱模型。"
        )
    try:
        frame_query = sing_frame_audio_query(vv_notes, base_url)
        wav_bytes = frame_synthesis(frame_query, sing_id, base_url)
    except Exception as e:
        raise RuntimeError(f"VOICEVOX 歌唱合成失败: {e}") from e

    data, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32")
    if len(data.shape) > 1:
        data = data.mean(axis=1)
    if sr != sample_rate:
        ratio = sample_rate / sr
        new_len = int(len(data) * ratio)
        indices = np.linspace(0, len(data) - 1, new_len)
        data = np.interp(indices, np.arange(len(data)), data).astype(np.float32)
    return data


def _synthesize_section(
    score: ParsedScore,
    section_index: int,
    sample_rate: int,
    max_duration_seconds: Optional[float],
    voice_id_override: Optional[int],
    base_url: str = VOICEVOX_BASE,
    cache_key: Optional[str] = None,
) -> Optional[tuple[np.ndarray, float]]:
    """内部：合成单篇章歌声。支持多个人声轨，混合输出。"""
    if cache_key:
        cached = get_cached_audio(cache_key)
        if cached is not None:
            return cached

    section_lyrics = score.section_lyrics or []
    if section_index >= len(section_lyrics):
        return None
    sec_lyrics = section_lyrics[section_index]

    # 收集所有带 voice_id 的条目（有 override 时覆盖）
    entries: list[tuple[int, int, int, list[str], int]] = []  # (part_idx, voice_id, melody_part, part_queue, volume)
    seen_parts: set[int] = set()
    for part_idx, syllables, voice_id, melody_part, volume in sec_lyrics:
        effective_id = voice_id_override if voice_id_override is not None else voice_id
        if effective_id is not None and syllables and part_idx not in seen_parts:
            seen_parts.add(part_idx)
            part_queue: list[str] = []
            for pidx, s, _, _, _ in sec_lyrics:
                if pidx == part_idx:
                    part_queue.extend(s)
            entries.append((part_idx, effective_id, melody_part, part_queue, volume))

    if not entries:
        return None

    mixed: Optional[np.ndarray] = None
    max_len = 0

    for part_idx, voice_id, melody_part, part_queue, volume in entries:
        notes = _build_sing_notes_for_entry(
            score, section_index, part_idx, voice_id, melody_part, part_queue, max_duration_seconds
        )
        if not notes:
            continue
        audio = _synthesize_one_voice(notes, voice_id, sample_rate, base_url)
        if audio is not None:
            gain = volume / 100.0
            audio = audio * gain
            if mixed is None:
                mixed = np.zeros_like(audio)
            target_len = max(len(mixed), len(audio))
            if len(mixed) < target_len:
                mixed = np.pad(mixed, (0, target_len - len(mixed)), mode="constant")
            if len(audio) < target_len:
                audio = np.pad(audio, (0, target_len - len(audio)), mode="constant")
            mixed[:target_len] += audio[:target_len]
            max_len = max(max_len, target_len)

    if mixed is None or max_len == 0:
        return None
    out = mixed[:max_len]
    max_val = np.abs(out).max()
    if max_val > 1.0:
        out = out / max_val * 0.95
    duration = max_len / sample_rate
    if cache_key:
        save_audio_to_cache(cache_key, out, duration)
    return (out, duration)


def synthesize_lyrics(
    score: ParsedScore,
    section_index: int,
    sample_rate: int = 44100,
    max_duration_seconds: Optional[float] = None,
    voice_id_override: Optional[int] = None,
    base_url: str = VOICEVOX_BASE,
    cache_key: Optional[str] = None,
) -> Optional[tuple[np.ndarray, float]]:
    """
    合成指定篇章的歌词歌声。
    返回 (audio_float32, duration_seconds) 或 None（无 voice_id 或合成失败）
    cache_key 有值时优先从缓存加载，生成后写入缓存。
    """
    return _synthesize_section(
        score, section_index, sample_rate, max_duration_seconds, voice_id_override, base_url, cache_key
    )


def synthesize_acappella(
    score_text: str,
    sample_rate: int = 44100,
    voice_id_override: Optional[int] = None,
    base_url: str = VOICEVOX_BASE,
) -> Optional[tuple[np.ndarray, float]]:
    """
    全曲清唱合成：仅 TTS + 歌声，不含 WAV 伴奏。
    voice_id_override: 若指定，将覆盖简谱中 \\lyrics 的 voice_id（用于在对话框中临时选用音色）
    返回 (audio_float32, duration_seconds) 或 None
    """
    from validator import parse
    from scheduler import schedule_segments

    try:
        parsed = parse(score_text)
    except Exception as e:
        raise ValueError(f"简谱解析失败: {e}") from e
    segments = schedule_segments(parsed)
    if not segments:
        return None

    try:
        from tts_helper import generate_tts_audio
    except ImportError:
        def generate_tts_audio(*args, **kwargs):
            return None

    audio_parts: list[np.ndarray] = []
    for seg in segments:
        for tts in seg.tts_before:
            tts_result = generate_tts_audio(tts.text, tts.lang, sample_rate, voice_id=getattr(tts, "voice_id", None))
            if tts_result:
                audio_parts.append(tts_result[0])
        if seg.notes:
            seg_dur = max(n.start_time + n.duration for n in seg.notes)
            can_sing = has_lyrics_voice(parsed, seg.section_index) or (
                voice_id_override is not None and has_lyrics_syllables(parsed, seg.section_index)
            )
            if can_sing:
                # 不截断，生成完整歌词内容
                lyrics_ck = cache_key_lyrics(score_text, seg.section_index, sample_rate)
                sing_result = _synthesize_section(
                    parsed, seg.section_index, sample_rate,
                    max_duration_seconds=None,
                    voice_id_override=voice_id_override,
                    base_url=base_url,
                    cache_key=lyrics_ck,
                )
                if sing_result:
                    seg_audio, _ = sing_result
                    target_len = max(len(seg_audio), int(seg_dur * sample_rate) + 1)
                    if len(seg_audio) < target_len:
                        seg_audio = np.pad(seg_audio, (0, target_len - len(seg_audio)), mode="constant")
                    elif len(seg_audio) > target_len:
                        seg_audio = seg_audio[:target_len]
                    audio_parts.append(seg_audio)

    if not audio_parts:
        return None
    audio = np.concatenate([a for a in audio_parts if len(a) > 0])
    if len(audio) == 0:
        return None
    max_val = np.abs(audio).max()
    if max_val > 1.0:
        audio = audio / max_val * 0.95
    return (audio, len(audio) / sample_rate)
