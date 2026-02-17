"""
TTS 辅助：生成语音音频，支持中/日/英
使用 edge-tts（需网络），若不可用则跳过
"""
from pathlib import Path
from typing import Optional

import numpy as np

# 语言 -> edge-tts 语音
VOICE_MAP = {
    "zh-CN": "zh-CN-XiaoxiaoNeural",
    "ja-JP": "ja-JP-NanamiNeural",
    "en-US": "en-US-JennyNeural",
}

_tts_warned: set[str] = set()


def generate_tts_audio(text: str, lang: str, sample_rate: int = 44100) -> Optional[tuple[np.ndarray, float]]:
    """
    生成 TTS 音频，返回 (float32 mono 数组, 时长秒)。
    若失败返回 None。
    """
    global _tts_warned
    try:
        import asyncio
        import edge_tts
    except ImportError:
        if "import" not in _tts_warned:
            _tts_warned.add("import")
            print("[TTS] 未安装 edge-tts，请运行: pip install edge-tts pydub")
        return None

    voice = VOICE_MAP.get(lang, "en-US-JennyNeural")
    tmp = Path(".tts_tmp.mp3")

    async def _run():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(tmp))

    try:
        asyncio.run(_run())
    except Exception as e:
        if "network" not in _tts_warned:
            _tts_warned.add("network")
            print(f"[TTS] 生成失败（需网络）: {e}")
        return None

    if not tmp.exists():
        return None

    # 优先用 audioread（macOS 可用 Core Audio，无需 ffmpeg）
    try:
        import audioread
        with audioread.audio_open(str(tmp)) as f:
            sr = f.samplerate
            chunks = []
            for buf in f:
                chunks.append(np.frombuffer(buf, dtype=np.int16))
            if not chunks:
                raise ValueError("empty audio")
            samples = np.concatenate(chunks).astype(np.float32) / 32768.0
            if f.channels > 1:
                samples = samples.reshape(-1, f.channels).mean(axis=1)
            if sr != sample_rate:
                ratio = sample_rate / sr
                new_len = int(len(samples) * ratio)
                indices = np.linspace(0, len(samples) - 1, new_len)
                samples = np.interp(indices, np.arange(len(samples)), samples).astype(np.float32)
            duration = len(samples) / sample_rate
            return samples, duration
    except ImportError:
        pass
    except Exception as e:
        if "decode" not in _tts_warned:
            _tts_warned.add("decode")
            print(f"[TTS] audioread 失败: {e}")

    # 备选：pydub（需 ffmpeg）
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_mp3(str(tmp))
        samples = np.array(seg.get_array_of_samples(), dtype=np.float32) / 32768.0
        if seg.channels > 1:
            samples = samples.reshape(-1, seg.channels).mean(axis=1)
        sr = seg.frame_rate
        if sr != sample_rate:
            ratio = sample_rate / sr
            new_len = int(len(samples) * ratio)
            indices = np.linspace(0, len(samples) - 1, new_len)
            samples = np.interp(indices, np.arange(len(samples)), samples).astype(np.float32)
        duration = len(samples) / sample_rate
        return samples, duration
    except Exception as e:
        if "decode" not in _tts_warned:
            _tts_warned.add("decode")
            print(f"[TTS] 音频解码失败（请安装 audioread 或 ffmpeg）: {e}")
        return None
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
