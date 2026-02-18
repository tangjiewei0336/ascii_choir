"""
音频播放器：加载 WAV 并按时序混音播放，支持 TTS
"""
import numpy as np
import soundfile as sf
import sounddevice as sd
from pathlib import Path
from typing import Callable, Optional

from sound_loader import load_sound_library, get_default_sound_path
from scheduler import ScheduledNote, schedule, schedule_segments, ScheduledSegment
from validator import parse

try:
    from tts_helper import generate_tts_audio
except ImportError:
    def generate_tts_audio(*args, **kwargs):
        return None

try:
    from lyrics_synth import synthesize_lyrics, has_lyrics_voice
except ImportError:
    def synthesize_lyrics(*args, **kwargs):
        return None

    def has_lyrics_voice(*args, **kwargs):
        return False


class Player:
    def __init__(self, sound_library_path: Optional[str] = None):
        path = sound_library_path or get_default_sound_path()
        self.sound_map = load_sound_library(path)
        self.sample_rate = 44100  # 统一采样率
        self._cache: dict[int, np.ndarray] = {}
        self._stop_requested = False
        self._on_progress: Optional[Callable[[float, float], None]] = None
    
    def set_progress_callback(self, callback: Callable[[float, float], None]):
        """设置进度回调 (current_time, total_duration)"""
        self._on_progress = callback
    
    def _load_wav(self, midi: int) -> Optional[np.ndarray]:
        """加载并缓存单个音色的 WAV，转为 mono float32"""
        if midi in self._cache:
            return self._cache[midi]
        path = self.sound_map.get(midi)
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
            self._cache[midi] = data
            return data
        except Exception:
            return None
    
    def _render_notes(self, notes: list[ScheduledNote]) -> tuple[np.ndarray, float]:
        """
        将 ScheduledNote 列表渲染为混合后的音频数组。
        返回 (audio_array, total_duration_seconds)
        """
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
            for midi in n.midis:
                wav = self._load_wav(midi)
                if wav is None:
                    continue
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
    
    def play_score(self, score_text: str) -> float:
        """
        解析并播放简谱，返回总时长(秒)。
        支持篇章间 TTS（\\tts{text}{lang}）。
        阻塞直到播放完成或 stop 被调用。
        """
        self._stop_requested = False
        parsed = parse(score_text)
        segments = schedule_segments(parsed)

        if not segments:
            return 0.0

        audio_parts: list[np.ndarray] = []
        total_duration = 0.0

        for seg in segments:
            # TTS（篇章前）
            for tts in seg.tts_before:
                if self._stop_requested:
                    break
                voice_id = getattr(tts, "voice_id", None)
                tts_result = generate_tts_audio(tts.text, tts.lang, self.sample_rate, voice_id=voice_id)
                if tts_result:
                    tts_audio, tts_dur = tts_result
                    audio_parts.append(tts_audio)
                    total_duration += tts_dur

            if self._stop_requested:
                break

            # 音符：有 \\lyrics{...}{voice_id} 时用 VOICEVOX 歌唱合成，否则用 WAV
            if seg.notes:
                seg_dur = max(n.start_time + n.duration for n in seg.notes)
                if has_lyrics_voice(parsed, seg.section_index):
                    sing_result = synthesize_lyrics(
                        parsed, seg.section_index, self.sample_rate, max_duration_seconds=seg_dur
                    )
                    if sing_result:
                        seg_audio, _ = sing_result
                        # 若歌声短于段落，末尾补静音
                        target_len = int(seg_dur * self.sample_rate) + 1
                        if len(seg_audio) < target_len:
                            seg_audio = np.pad(seg_audio, (0, target_len - len(seg_audio)), mode="constant")
                        elif len(seg_audio) > target_len:
                            seg_audio = seg_audio[:target_len]
                        audio_parts.append(seg_audio)
                        total_duration += seg_dur
                    else:
                        seg_audio, seg_dur = self._render_notes(seg.notes)
                        audio_parts.append(seg_audio)
                        total_duration += seg_dur
                else:
                    seg_audio, seg_dur = self._render_notes(seg.notes)
                    audio_parts.append(seg_audio)
                    total_duration += seg_dur

        if not audio_parts:
            return 0.0

        audio = np.concatenate([a for a in audio_parts if len(a) > 0])
        if len(audio) == 0:
            return 0.0

        # 归一化
        max_val = np.abs(audio).max()
        if max_val > 1.0:
            audio = audio / max_val * 0.95

        audio_stereo = np.column_stack([audio, audio])

        sd.play(audio_stereo, self.sample_rate)

        step_ms = 50
        elapsed = 0.0
        while elapsed < total_duration and not self._stop_requested:
            sd.sleep(step_ms)
            elapsed += step_ms / 1000.0
            if self._on_progress:
                self._on_progress(min(elapsed, total_duration), total_duration)

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
