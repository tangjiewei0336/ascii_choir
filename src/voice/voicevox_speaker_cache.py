"""
音色头像：从内嵌的 speaker_info_bundled.json 读取，供本地库模式使用。
"""
import json
import sys
from pathlib import Path
from typing import Optional


def _get_bundled_path() -> Path:
    """打包后 __file__ 可能不可靠，优先用 sys._MEIPASS"""
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / "src" / "voice" / "speaker_info_bundled.json"
    return Path(__file__).parent / "speaker_info_bundled.json"


_BUNDLED_PATH = _get_bundled_path()
_cache: dict[str, dict] | None = None


def _load_bundled() -> dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    if not _BUNDLED_PATH.exists():
        _cache = {}
        return _cache
    try:
        _cache = json.loads(_BUNDLED_PATH.read_text(encoding="utf-8"))
        return _cache
    except Exception:
        _cache = {}
        return _cache


def load_speaker_info_from_cache(speaker_uuid: str) -> Optional[dict]:
    """
    从内嵌的 speaker_info_bundled.json 加载 speaker_info。
    返回与 /speaker_info API 相同格式的 dict，图片为 base64。
    会添加 "_format": "base64" 供调用方识别。
    无缓存时返回 None。
    """
    data = _load_bundled()
    info = data.get(str(speaker_uuid))
    if info is not None:
        info = dict(info)
        info["_format"] = "base64"
    return info
