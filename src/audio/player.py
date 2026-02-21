"""
音频播放器：加载 WAV 并按时序混音播放，支持 TTS
"""
import numpy as np

from src.audio.audio_cache import (
    cache_key_lyrics,
    cache_key_play,
    get_cached_audio,
    save_audio_to_cache,
)
import soundfile as sf
import sounddevice as sd
from pathlib import Path
from typing import Callable, Optional

from src.audio.sound_loader import load_sound_library, get_default_sound_path
from src.instruments.instrument_registry import get_instrument_path_for_note
from src.core.scheduler import ScheduledNote, schedule, schedule_segments, ScheduledSegment
from src.core.validator import parse

try:
    from src.voice.tts_helper import generate_tts_audio
except ImportError:
    def generate_tts_audio(*args, **kwargs):
        return None

try:
    from src.voice.lyrics_synth import synthesize_lyrics, has_lyrics_voice, get_lyrics_part_indices
except ImportError:
    def synthesize_lyrics(*args, **kwargs):
        return None

    def has_lyrics_voice(*args, **kwargs):
        return False

    def get_lyrics_part_indices(*args, **kwargs):
        return []


class Player:
    def __init__(self, sound_library_path: Optional[str] = None):
        path = sound_library_path or get_default_sound_path()
        self._sound_library_path = path
        self.sound_map = load_sound_library(path)
        self.sample_rate = 44100  # 统一采样率
        self._cache: dict[int, np.ndarray] = {}
        self._stop_requested = False
        self._on_progress: Optional[Callable[..., None]] = None

    def set_progress_callback(self, callback: Callable[..., None]):
        """设置进度回调 (current, total, phase, status=None)。phase 为 'generating' 或 'playing'，status 为可选自定义状态文本"""
        self._on_progress = callback
    
    def _load_wav(
        self,
        midi: int,
        instrument: str = "grand_piano",
        chord_midis: list[int] | None = None,
    ) -> Optional[np.ndarray]:
        """加载并缓存单个音色的 WAV，转为 mono float32。按 instrument 选择音色库。"""
        lib_path = get_instrument_path_for_note(instrument, midi, chord_midis)
        if not lib_path:
            lib_path = get_default_sound_path()
        cache_key = (lib_path, midi)
        if cache_key in self._cache:
            return self._cache[cache_key]
        sound_map = load_sound_library(lib_path)
        path = sound_map.get(midi)
        if not path or not Path(path).exists():
            return None
        try:
            data, sr = sf.read(path, dtype="float32")
            if len(data.shape) > 1:
                data = data.mean(axis=1)
            if sr != self.sample_rate:
                # 简单线性重采样（生产环境可用 scipy.signal.resample）
                ratio = self.sample_rate / sr
                new_len = int(len(data) * ratio)
                indices = np.linspace(0, len(data) - 1, new_len)
                data = np.interp(indices, np.arange(len(data)), data)
            self._cache[cache_key] = data
            return data
        except Exception:
            return None
    
    def _merge_continuation_notes(self, notes: list[ScheduledNote]) -> list[ScheduledNote]:
        """
        合并 is_continuation 事件：将连音延续与前一同音高音符合并，避免重复触发。
        和弦内被 tie 的音会拆出单独延长，以保持和弦其他音相同时值。
        """
        result: list[ScheduledNote] = []
        for n in notes:
            if not getattr(n, "is_continuation", False) or len(n.midis) != 1:
                result.append(n)
                continue
            midi = n.midis[0]
            # 查找前一音符：结束时间等于本音符开始，且包含该 midi
            merged = False
            for i in range(len(result) - 1, -1, -1):
                prev = result[i]
                prev_end = prev.start_time + prev.duration
                if abs(prev_end - n.start_time) > 1e-6:
                    continue
                if midi not in prev.midis:
                    continue
                # 找到：合并延续
                merged = True
                if len(prev.midis) == 1:
                    # 单音：直接延长
                    result[i] = ScheduledNote(
                        prev.start_time, prev.duration + n.duration,
                        prev.midis, prev.volume, prev.part_index,
                        is_continuation=False,
                    )
                else:
                    # 和弦：拆出被 tie 的音单独延长，其余保持
                    others = [m for m in prev.midis if m != midi]
                    result.pop(i)
                    if others:
                        result.append(ScheduledNote(
                            prev.start_time, prev.duration, others,
                            prev.volume, prev.part_index, is_continuation=False,
                        ))
                    result.append(ScheduledNote(
                        prev.start_time, prev.duration + n.duration, [midi],
                        prev.volume, prev.part_index, is_continuation=False,
                    ))
                    # 和弦拆开后需按 start_time 排序
                    result.sort(key=lambda x: (x.start_time, -len(x.midis)))
                break
            if not merged:
                result.append(n)
        return result

    def _render_notes(self, notes: list[ScheduledNote]) -> tuple[np.ndarray, float]:
        """
        将 ScheduledNote 列表渲染为混合后的音频数组。
        返回 (audio_array, total_duration_seconds)
        """
        notes = self._merge_continuation_notes(notes)
        if not notes:
            return np.array([], dtype=np.float32), 0.0
        
        end_times = [
            n.start_time + n.duration
            for n in notes
        ]
        total_duration = max(end_times)
        total_samples = int(total_duration * self.sample_rate) + 1
        mix = np.zeros(total_samples, dtype=np.float32)
        
        for n in notes:
            inst = getattr(n, "instrument", "grand_piano")
            chord_midis = n.midis if len(n.midis) > 1 else None
            for midi in n.midis:
                wav = self._load_wav(midi, instrument=inst, chord_midis=chord_midis)
                if wav is None:
                    from src.instruments.instrument_registry import midi_to_note_name
                    note_name = midi_to_note_name(midi)
                    chord_str = f" 和弦 {', '.join(midi_to_note_name(m) for m in chord_midis)}" if chord_midis else ""
                    raise RuntimeError(
                        f"音符 {note_name}（MIDI {midi}）{chord_str} 超出 [{inst}] 音域，无法弹奏"
                    )
                start_sample = int(n.start_time * self.sample_rate)
                # 裁剪或填充以匹配 duration
                target_len = int(n.duration * self.sample_rate)
                if len(wav) > target_len:
                    wav = wav[:target_len]
                elif len(wav) < target_len:
                    wav = np.pad(wav, (0, target_len - len(wav)), mode="constant")
                
                gain = n.volume
                end_sample = min(start_sample + len(wav), total_samples)
                actual_len = end_sample - start_sample
                mix[start_sample:end_sample] += wav[:actual_len] * gain
        
        # 归一化防止削波
        max_val = np.abs(mix).max()
        if max_val > 1.0:
            mix = mix / max_val * 0.95
        
        return mix, total_duration

    def render_audio(self, score_text: str) -> tuple[np.ndarray, float] | None:
        """
        解析并生成简谱音频，不播放。返回 (audio_mono, duration_seconds) 或 None（空曲目）。
        支持 TTS、歌词合成，使用与 play_score 相同的缓存。
        """
        self._stop_requested = False
        lib_path = self._sound_library_path
        ck = cache_key_play(score_text, lib_path, self.sample_rate)
        cached = get_cached_audio(ck)
        if cached is not None:
            return cached

        parsed = parse(score_text)
        segments = schedule_segments(parsed)
        if not segments:
            return None

        n_seg = len(segments)
        total_work = max(n_seg, 1)
        if self._on_progress:
            self._on_progress(0, total_work, "generating", "准备中...")

        audio_parts: list[np.ndarray] = []
        total_duration = 0.0
        seg_idx = 0

        while seg_idx < len(segments):
            seg = segments[seg_idx]
            for tts in seg.tts_before:
                if self._stop_requested:
                    break
                if self._on_progress:
                    self._on_progress(seg_idx, total_work, "generating", f"TTS/VOICEVOX 生成中 {seg_idx + 1}/{total_work}")
                voice_id = getattr(tts, "voice_id", None)
                tts_result = generate_tts_audio(tts.text, tts.lang, self.sample_rate, voice_id=voice_id)
                if tts_result:
                    tts_audio, tts_dur = tts_result
                    audio_parts.append(tts_audio)
                    total_duration += tts_dur

            if self._stop_requested:
                break

            if not seg.notes:
                seg_idx += 1
                if self._on_progress:
                    self._on_progress(seg_idx, total_work, "generating", f"段 {seg_idx}/{n_seg} 完成")
                continue

            lyrics_parts = get_lyrics_part_indices(parsed, seg.section_index) if has_lyrics_voice(parsed, seg.section_index) else []

            if lyrics_parts:
                merge_segs: list[ScheduledSegment] = [seg]
                j = seg_idx + 1
                while j < len(segments) and not segments[j].tts_before:
                    merge_segs.append(segments[j])
                    j += 1

                if self._on_progress:
                    self._on_progress(seg_idx, total_work, "generating", f"VOICEVOX 歌声生成中 {seg_idx + 1}-{j}/{n_seg}")

                lyrics_ck = cache_key_lyrics(score_text, seg.section_index, self.sample_rate)
                sing_result = synthesize_lyrics(
                    parsed, seg.section_index, self.sample_rate, max_duration_seconds=None, cache_key=lyrics_ck
                )
                voice_audio = sing_result[0] if sing_result else np.array([], dtype=np.float32)

                lyrics_set = set(lyrics_parts)
                t_offset = 0.0
                accomp_parts: list[tuple[np.ndarray, float]] = []
                for mseg in merge_segs:
                    other_notes = [n for n in mseg.notes if n.part_index not in lyrics_set]
                    if other_notes:
                        acc, acc_dur = self._render_notes(other_notes)
                        if len(acc) > 0:
                            accomp_parts.append((acc, t_offset))
                    t_offset += max(n.start_time + n.duration for n in mseg.notes)

                target_len = max(len(voice_audio), int(t_offset * self.sample_rate) + 1)
                mix = np.zeros(target_len, dtype=np.float32)
                if len(voice_audio) > 0:
                    mix[:len(voice_audio)] += voice_audio
                for acc_audio, offset in accomp_parts:
                    start_samp = int(offset * self.sample_rate)
                    end_samp = min(start_samp + len(acc_audio), target_len)
                    mix[start_samp:end_samp] += acc_audio[: end_samp - start_samp]

                audio_parts.append(mix)
                total_duration += target_len / self.sample_rate
                seg_idx = j
            else:
                if self._on_progress:
                    self._on_progress(seg_idx, total_work, "generating", f"伴奏生成中 {seg_idx + 1}/{total_work}")
                seg_audio, seg_dur = self._render_notes(seg.notes)
                audio_parts.append(seg_audio)
                total_duration += seg_dur
                seg_idx += 1
            if self._on_progress:
                self._on_progress(seg_idx, total_work, "generating", f"段 {seg_idx}/{n_seg} 完成")

        if not audio_parts:
            return None

        audio = np.concatenate([a for a in audio_parts if len(a) > 0])
        if len(audio) == 0:
            return None

        max_val = np.abs(audio).max()
        if max_val > 1.0:
            audio = audio / max_val * 0.95

        total_duration = len(audio) / self.sample_rate
        save_audio_to_cache(ck, audio, total_duration)
        return audio, total_duration
    
    def play_score(self, score_text: str) -> float:
        """
        解析并播放简谱，返回总时长(秒)。
        支持篇章间 TTS（\\tts{text}{lang}）。
        阻塞直到播放完成或 stop 被调用。
        通过哈希缓存已生成的音频，重复播放同一曲目时直接加载缓存。
        """
        self._stop_requested = False

        # 检查全曲缓存
        lib_path = self._sound_library_path
        ck = cache_key_play(score_text, lib_path, self.sample_rate)
        cached = get_cached_audio(ck)
        if cached is not None:
            audio, total_duration = cached
            audio_stereo = np.column_stack([audio, audio])
            sd.play(audio_stereo, self.sample_rate)
            step_ms = 50
            elapsed = 0.0
            while elapsed < total_duration and not self._stop_requested:
                sd.sleep(step_ms)
                elapsed += step_ms / 1000.0
                if self._on_progress:
                    self._on_progress(min(elapsed, total_duration), total_duration, "playing")
            if self._stop_requested:
                sd.stop()
            return total_duration

        parsed = parse(score_text)
        segments = schedule_segments(parsed)

        if not segments:
            return 0.0

        n_seg = len(segments)
        total_tts = sum(len(s.tts_before) for s in segments)
        # 以 segment 数为总量，便于显示「段 X/Y」（如 3 段 lyrics 显示 1/3、2/3、3/3）
        total_work = max(n_seg, 1)

        work_done = 0
        if self._on_progress:
            self._on_progress(0, total_work, "generating", "准备中...")

        # 先生成全部内容再播放，避免不同步。有歌词时合并相关篇章，避免唱完后再用 MIDI 重复演奏
        audio_parts: list[np.ndarray] = []
        total_duration = 0.0
        seg_idx = 0

        while seg_idx < len(segments):
            seg = segments[seg_idx]
            # TTS（篇章前）
            for tts in seg.tts_before:
                if self._stop_requested:
                    break
                if self._on_progress:
                    self._on_progress(seg_idx, total_work, "generating", f"TTS/VOICEVOX 生成中 {seg_idx + 1}/{total_work}")
                voice_id = getattr(tts, "voice_id", None)
                tts_result = generate_tts_audio(tts.text, tts.lang, self.sample_rate, voice_id=voice_id)
                if tts_result:
                    tts_audio, tts_dur = tts_result
                    audio_parts.append(tts_audio)
                    total_duration += tts_dur

            if self._stop_requested:
                break

            if not seg.notes:
                seg_idx += 1
                if self._on_progress:
                    self._on_progress(seg_idx, total_work, "generating", f"段 {seg_idx}/{n_seg} 完成")
                continue

            lyrics_parts = get_lyrics_part_indices(parsed, seg.section_index) if has_lyrics_voice(parsed, seg.section_index) else []

            if lyrics_parts:
                # 有歌词：合并本段及后续无 TTS 的篇章，歌声 + 伴奏一次性混合，避免唱完再 MIDI 重复
                merge_segs: list[ScheduledSegment] = [seg]
                j = seg_idx + 1
                while j < len(segments) and not segments[j].tts_before:
                    merge_segs.append(segments[j])
                    j += 1

                n_voices = len(lyrics_parts)
                if self._on_progress:
                    self._on_progress(seg_idx, total_work, "generating", f"VOICEVOX 歌声生成中 0/{n_voices} 声部")

                def _lyrics_progress(voice_cur: int, voice_tot: int):
                    if self._on_progress and voice_tot > 0:
                        frac = seg_idx + (voice_cur / voice_tot) * (j - seg_idx)
                        self._on_progress(
                            frac, total_work, "generating",
                            f"VOICEVOX 歌声生成中 {voice_cur}/{voice_tot} 声部"
                        )

                lyrics_ck = cache_key_lyrics(score_text, seg.section_index, self.sample_rate)
                sing_result = synthesize_lyrics(
                    parsed, seg.section_index, self.sample_rate,
                    max_duration_seconds=None, cache_key=lyrics_ck, on_progress=_lyrics_progress
                )
                voice_audio = sing_result[0] if sing_result else np.array([], dtype=np.float32)

                # 合并所有篇章的伴奏（排除所有人声轨），按时间偏移拼接
                lyrics_set = set(lyrics_parts)
                t_offset = 0.0
                accomp_parts: list[tuple[np.ndarray, float]] = []
                for mseg in merge_segs:
                    other_notes = [n for n in mseg.notes if n.part_index not in lyrics_set]
                    if other_notes:
                        acc, acc_dur = self._render_notes(other_notes)
                        if len(acc) > 0:
                            accomp_parts.append((acc, t_offset))
                    t_offset += max(n.start_time + n.duration for n in mseg.notes)

                target_len = max(
                    len(voice_audio),
                    int(t_offset * self.sample_rate) + 1,
                )
                mix = np.zeros(target_len, dtype=np.float32)
                if len(voice_audio) > 0:
                    mix[:len(voice_audio)] += voice_audio
                for acc_audio, offset in accomp_parts:
                    start_samp = int(offset * self.sample_rate)
                    end_samp = min(start_samp + len(acc_audio), target_len)
                    mix[start_samp:end_samp] += acc_audio[: end_samp - start_samp]

                audio_parts.append(mix)
                total_duration += target_len / self.sample_rate
                seg_idx = j
            else:
                if self._on_progress:
                    self._on_progress(seg_idx, total_work, "generating", f"伴奏生成中 {seg_idx + 1}/{total_work}")
                seg_audio, seg_dur = self._render_notes(seg.notes)
                audio_parts.append(seg_audio)
                total_duration += seg_dur
                seg_idx += 1
            if self._on_progress:
                self._on_progress(seg_idx, total_work, "generating", f"段 {seg_idx}/{n_seg} 完成")

        if not audio_parts:
            return 0.0

        audio = np.concatenate([a for a in audio_parts if len(a) > 0])
        if len(audio) == 0:
            return 0.0

        # 归一化
        max_val = np.abs(audio).max()
        if max_val > 1.0:
            audio = audio / max_val * 0.95

        # 保存到缓存供下次使用
        total_duration = len(audio) / self.sample_rate
        save_audio_to_cache(ck, audio, total_duration)

        audio_stereo = np.column_stack([audio, audio])

        sd.play(audio_stereo, self.sample_rate)

        step_ms = 50
        elapsed = 0.0
        while elapsed < total_duration and not self._stop_requested:
            sd.sleep(step_ms)
            elapsed += step_ms / 1000.0
            if self._on_progress:
                self._on_progress(min(elapsed, total_duration), total_duration, "playing")

        if self._stop_requested:
            sd.stop()

        return total_duration
    
    def stop(self):
        """请求停止播放"""
        self._stop_requested = True


def play_async(score_text: str, on_progress=None):
    """
    异步播放简谱（在后台线程）。
    on_progress: (current, total) -> None
    """
    import threading
    player = Player()
    if on_progress:
        player.set_progress_callback(on_progress)
    
    def run():
        player.play_score(score_text)
    
    t = threading.Thread(target=run)
    t.start()
    return player, t
