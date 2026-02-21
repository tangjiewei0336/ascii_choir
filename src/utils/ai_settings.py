"""
AI 服务设置：OpenAI、Dashscope（通义）等 API 配置。
"""
import json
import sys
from pathlib import Path
from typing import Optional

AI_PROVIDERS = ("openai", "dashscope", "custom")
AI_PROVIDER_LABELS = {"openai": "OpenAI", "dashscope": "通义千问 (Dashscope)", "custom": "自定义 (OpenAI 兼容)"}

_SETTINGS_FILENAME = "ai_settings.json"
_DEFAULT_ENDPOINT = "https://api.openai.com/v1"


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


def get_ai_provider() -> str:
    """当前选中的 AI 服务提供商：openai / dashscope / custom"""
    return _load_raw().get("ai_provider", "openai") or "openai"


def set_ai_provider(provider: str) -> None:
    if provider not in AI_PROVIDERS:
        provider = "openai"
    _save_key("ai_provider", provider)


def get_openai_apikey() -> str:
    return _load_raw().get("openai_apikey", "") or ""


def set_openai_apikey(key: str) -> None:
    _save_key("openai_apikey", (key or "").strip())


def get_openai_endpoint() -> str:
    return _load_raw().get("openai_endpoint", "") or _DEFAULT_ENDPOINT


def set_openai_endpoint(endpoint: str) -> None:
    _save_key("openai_endpoint", (endpoint or "").strip() or _DEFAULT_ENDPOINT)


def get_dashscope_api_key() -> str:
    return _load_raw().get("dashscope_api_key", "") or ""


def set_dashscope_api_key(key: str) -> None:
    _save_key("dashscope_api_key", (key or "").strip())


def _save_key(key: str, value: str) -> None:
    p = _settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = _load_raw()
        data[key] = value
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def get_all() -> dict:
    """获取全部 AI 设置"""
    raw = _load_raw()
    return {
        "ai_provider": raw.get("ai_provider", "openai") or "openai",
        "openai_apikey": raw.get("openai_apikey", "") or "",
        "openai_endpoint": raw.get("openai_endpoint", "") or _DEFAULT_ENDPOINT,
        "dashscope_api_key": raw.get("dashscope_api_key", "") or "",
    }


def save_all(provider: str, openai_apikey: str, openai_endpoint: str, dashscope_api_key: str) -> None:
    """保存全部 AI 设置"""
    p = _settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "ai_provider": provider if provider in AI_PROVIDERS else "openai",
            "openai_apikey": (openai_apikey or "").strip(),
            "openai_endpoint": (openai_endpoint or "").strip() or _DEFAULT_ENDPOINT,
            "dashscope_api_key": (dashscope_api_key or "").strip(),
        }
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
