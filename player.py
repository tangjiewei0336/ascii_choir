"""
音频播放器：加载 WAV 并按时序混音播放
"""
import numpy as np
import soundfile as sf
import sounddevice as sd
from pathlib import Path
from typing import Callable, Optional

from sound_loader import load_sound_library, get_default_sound_path
from scheduler import ScheduledNote, schedule
from validator import parse


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
        阻塞直到播放完成或 stop 被调用。
        """
        self._stop_requested = False
        parsed = parse(score_text)
        notes = schedule(parsed)
        
        if not notes:
            return 0.0
        
        audio, total_duration = self._render_notes(notes)
        # 转为 (n,1) 供 sounddevice 使用
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
