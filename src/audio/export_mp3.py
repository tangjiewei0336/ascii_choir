"""
导出音频为 MP3。依赖 pydub + ffmpeg。
若 ffmpeg 不可用则自动回退为 WAV。
"""
import numpy as np
from pathlib import Path
from typing import Tuple


def export_audio_to_mp3(
    audio: np.ndarray,
    sample_rate: int,
    path: str | Path,
) -> Tuple[str, bool]:
    """
    将 float32 单声道音频导出为 MP3（或 WAV 回退）。
    返回 (实际保存路径, 是否为 MP3)。
    """
    path = Path(path)
    mp3_path = path.with_suffix(".mp3")

    # float32 [-1, 1] -> int16
    audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    raw = audio_int16.tobytes()

    try:
        from pydub import AudioSegment

        seg = AudioSegment(
            data=raw,
            sample_width=2,
            frame_rate=sample_rate,
            channels=1,
        )
        seg.export(str(mp3_path), format="mp3")
        return str(mp3_path), True
    except Exception:
        # 回退：用 soundfile 导出 WAV
        import soundfile as sf

        wav_path = path.with_suffix(".wav")
        sf.write(str(wav_path), audio, sample_rate)
        return str(wav_path), False
