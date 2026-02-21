"""
音色加载模块：建立 MIDI 音符号到 WAV 文件的映射
"""
import os
import re
from pathlib import Path


def load_sound_library(library_path: str) -> dict[int, str]:
    """
    扫描音色库目录，建立 MIDI 音符号 -> WAV 文件路径的映射。
    文件名格式: German Concert D XXX 083.wav，XXX 为 021-108
    """
    mapping = {}
    path = Path(library_path)
    
    if not path.exists():
        return mapping
    
    pattern = re.compile(r"German Concert D (\d{3}) 083\.wav", re.IGNORECASE)
    
    for f in path.iterdir():
        if f.is_file() and f.suffix.lower() == ".wav":
            m = pattern.match(f.name)
            if m:
                midi_num = int(m.group(1))
                if 21 <= midi_num <= 108:
                    mapping[midi_num] = str(f.absolute())
    
    return mapping


def get_default_sound_path() -> str:
    """获取默认音色库路径（grand_piano）"""
    base = Path(__file__).parent.parent.parent
    return str(base / "sound_library" / "grand_piano")
