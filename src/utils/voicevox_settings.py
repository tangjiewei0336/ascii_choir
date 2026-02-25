"""
VOICEVOX 后端设置：本地库 (voicevox_core) 或 Docker (voicevox_engine)
"""
import json
import sys
from pathlib import Path

VOICEVOX_BACKENDS = ("auto", "core", "docker")
VOICEVOX_BACKEND_LABELS = {
    "auto": "自动选择（优先本地库）",
    "core": "本地库 (voicevox_core)",
    "docker": "Docker (voicevox_engine)",
}

_SETTINGS_FILENAME = "voicevox_settings.json"


def _config_dir() -> Path:
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Roaming" / "ASCII Choir"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ASCII Choir"
    return Path.home() / ".config" / "ascii_choir"


def _settings_path() -> Path:
    return _config_dir() / _SETTINGS_FILENAME


def _load_raw() -> dict:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_voicevox_backend() -> str:
    """当前选中的后端：auto / core / docker"""
    return _load_raw().get("voicevox_backend", "auto") or "auto"


def set_voicevox_backend(backend: str) -> None:
    if backend not in VOICEVOX_BACKENDS:
        backend = "auto"
    p = _settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = _load_raw()
        data["voicevox_backend"] = backend
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
