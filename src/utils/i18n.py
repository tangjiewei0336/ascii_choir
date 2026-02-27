"""
多语言支持：中文、英文、日文。
语言可在设置中切换，重启后生效。
"""
import json
import sys
from pathlib import Path

SUPPORTED_LANGUAGES = ("zh", "en", "ja")
LANGUAGE_LABELS = {
    "zh": "简体中文",
    "en": "English",
    "ja": "日本語",
}

_SETTINGS_FILENAME = "app_settings.json"


def _locales_dir() -> Path:
    """locales 目录：开发时用 src/locales，打包后用 app_root/locales"""
    try:
        from src.utils.frozen_path import get_app_root
        return get_app_root() / "src" / "locales"
    except Exception:
        return Path(__file__).resolve().parent.parent / "locales"

_translations: dict[str, dict[str, str]] = {}
_current_lang = "zh"


def _config_dir() -> Path:
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Roaming" / "ASCII Choir"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ASCII Choir"
    return Path.home() / ".config" / "ascii_choir"


def _settings_path() -> Path:
    return _config_dir() / _SETTINGS_FILENAME


def _load_translations(lang: str) -> dict[str, str]:
    """加载指定语言的翻译"""
    if lang in _translations:
        return _translations[lang]
    p = _locales_dir() / f"{lang}.json"
    if p.exists():
        try:
            _translations[lang] = json.loads(p.read_text(encoding="utf-8"))
            return _translations[lang]
        except Exception:
            pass
    _translations[lang] = {}
    return _translations[lang]


def get_language() -> str:
    """当前语言：zh / en / ja"""
    p = _settings_path()
    if not p.exists():
        return "zh"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        lang = data.get("language", "zh") or "zh"
        return lang if lang in SUPPORTED_LANGUAGES else "zh"
    except Exception:
        return "zh"


def set_language(lang: str) -> None:
    """设置语言并保存"""
    global _current_lang
    if lang not in SUPPORTED_LANGUAGES:
        lang = "zh"
    _current_lang = lang
    p = _settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        data["language"] = lang
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _(msg: str) -> str:
    """
    翻译函数。传入中文原文，返回当前语言下的译文。
    zh 时返回原文；en/ja 时返回对应翻译，无则回退到原文。
    """
    global _current_lang
    if _current_lang == "zh":
        return msg
    trans = _load_translations(_current_lang)
    return trans.get(msg, msg)


def _init_lang() -> None:
    """初始化当前语言（从设置加载）"""
    global _current_lang
    _current_lang = get_language()
