"""
VOICEVOX API 客户端
根据设置使用本地 voicevox_core 或 Docker voicevox_engine (http://localhost:50021)
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Literal, Optional

VOICEVOX_BASE = "http://127.0.0.1:50021"


def _is_core_available() -> bool:
    """voicevox_core 是否可用"""
    try:
        from src.voice.voicevox_core_backend import is_core_available
        return is_core_available()
    except Exception:
        return False


def _use_core() -> bool:
    """是否使用本地 voicevox_core 后端（受设置控制）"""
    from src.utils.voicevox_settings import get_voicevox_backend
    backend = get_voicevox_backend()
    if backend == "docker":
        return False
    if backend == "core":
        return _is_core_available()
    # auto: core 可用则用 core
    return _is_core_available()


def get_effective_voicevox_mode() -> Literal["core", "docker"]:
    """当前实际使用的模式，用于 UI 显示"""
    if _use_core():
        return "core"
    return "docker"


def get_voicevox_connection_hint() -> str:
    """连接失败时的提示文案（根据当前设置）"""
    from src.utils.voicevox_settings import get_voicevox_backend
    backend = get_voicevox_backend()
    if backend == "core":
        if _is_core_available():
            return "本地库异常，请重试"
        return "请安装 voicevox_core 和音声模型（音色 → VOICEVOX 音声模型管理）"
    return "请确保 voicevox_engine 已启动 (http://localhost:50021)"


def get_voicevox_mode_label() -> str:
    """当前模式显示文案，用于音色面板"""
    from src.utils.voicevox_settings import get_voicevox_backend
    backend = get_voicevox_backend()
    if backend == "core" and not _is_core_available():
        return "本地库（未就绪，请安装 voicevox_core 和音声模型）"
    mode = get_effective_voicevox_mode()
    return "本地库 (voicevox_core)" if mode == "core" else "Docker (voicevox_engine)"

# 音色名称 -> 利用規約・クレジット情報
VOICE_LEGAL_INFO: dict[str, str] = {
    "東北きりたん": """## 東北きりたん

東北きりたんの音声ライブラリを用いて生成した音声は、
「VOICEVOX:東北きりたん」とクレジットを記載すれば、商用・非商用で利用可能です。

利用規約の詳細は以下をご確認ください。
https://zunko.jp/con_ongen_kiyaku.html""",
    "東北イタコ": """## 東北イタコ

東北イタコの音声ライブラリを用いて生成した音声は、
「VOICEVOX:東北イタコ」とクレジットを記載すれば、商用・非商用で利用可能です。

利用規約の詳細は以下をご確認ください。
https://zunko.jp/con_ongen_kiyaku.html""",
    "あんこもん": """## あんこもん

あんこもんの音声ライブラリを用いて生成した音声は、
「VOICEVOX:あんこもん」とクレジットを記載すれば、商用・非商用で利用可能です。

利用規約の詳細は以下をご確認ください。
https://zunko.jp/con_ongen_kiyaku.html""",
}


def _request(method: str, url: str, data: Optional[bytes] = None, headers: Optional[dict] = None) -> tuple[int, bytes]:
    """发送 HTTP 请求，返回 (status_code, body)"""
    if data:
        try:
            body_str = data.decode("utf-8", errors="replace")
            if body_str.strip().startswith(("{", "[")):
                print(f"[VOICEVOX] {method} {url}\n[VOICEVOX] 请求 JSON:\n{body_str}")
        except Exception:
            pass
    req = urllib.request.Request(url, data=data, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if data and "Content-Type" not in (headers or {}):
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b""
    except urllib.error.URLError as e:
        raise ConnectionError(f"无法连接 VOICEVOX 引擎: {e.reason}") from e


def _format_error(status: int, body: bytes, endpoint: str) -> str:
    """格式化 API 错误信息，502 等给出排查建议"""
    body_str = body.decode("utf-8", errors="replace").strip() or "(无响应体)"
    msg = f"VOICEVOX {endpoint} 返回 {status}: {body_str}"
    if status == 502:
        msg += "\n\n502 常见原因：引擎未启动、已崩溃或代理异常。请确认 voicevox_engine 已运行，可访问 http://127.0.0.1:50021/docs 测试。"
    elif status == 500 and "sing" in endpoint.lower():
        msg += "\n\n歌唱 API 500：BPM 过快时歌手可能唱不出来，可尝试降低 \\bpm 数值。"
    elif status in (0, -1) or "Connection" in body_str:
        msg += "\n\n请确认 voicevox_engine 已启动 (默认端口 50021)。"
    return msg


def fetch_speakers(base_url: str = VOICEVOX_BASE) -> list[dict]:
    """
    获取可用音色列表。
    返回 [{"name": "角色名", "speaker_uuid": "...", "styles": [{"id": 1, "name": "风格名"}, ...]}, ...]
    本地库模式下始终返回完整列表（speakers_full_bundled.json），供音色面板展示全部角色；
    未下载的由 is_speaker_available 置灰。不依赖 get_metas_core（仅返回已加载模型角色，会导致只显示部分）。
    """
    from src.utils.voicevox_settings import get_voicevox_backend
    if get_voicevox_backend() == "core":
        from src.voice.voicevox_speaker_catalog import get_full_speakers_for_display
        return get_full_speakers_for_display()
    status, body = _request("GET", f"{base_url}/speakers")
    if status != 200:
        raise RuntimeError(_format_error(status, body, "/speakers"))
    data = json.loads(body.decode("utf-8"))
    return data


def fetch_speaker_info(
    speaker_uuid: str,
    base_url: str = VOICEVOX_BASE,
    resource_format: str = "url",
) -> dict:
    """
    获取角色详细信息，包含头像（portrait）和图标（icon）。
    resource_format: "url" 返回可访问的 URL；"base64" 返回 base64 编码的图片数据。
    返回 {"policy": str, "portrait": str, "style_infos": [{"id": int, "icon": str, "portrait": str, ...}]}
    本地库模式下无 portrait/icon，返回空结构。
    """
    if _use_core():
        from src.voice.voicevox_speaker_cache import load_speaker_info_from_cache
        cached = load_speaker_info_from_cache(speaker_uuid)
        if cached is not None:
            return cached
        # 无缓存时返回空结构（音色列表仍可用，仅无头像）
        return {"policy": "", "portrait": "", "style_infos": []}
    url = f"{base_url}/speaker_info?speaker_uuid={urllib.parse.quote(speaker_uuid)}&resource_format={resource_format}"
    status, body = _request("GET", url)
    if status != 200:
        raise RuntimeError(_format_error(status, body, "/speaker_info"))
    return json.loads(body.decode("utf-8"))


def audio_query(text: str, speaker_id: int, base_url: str = VOICEVOX_BASE) -> dict:
    """
    创建语音合成查询。
    返回 AudioQuery JSON，可编辑后传给 synthesis。
    """
    url = f"{base_url}/audio_query?speaker={speaker_id}&text={urllib.parse.quote(text)}"
    status, body = _request("POST", url)
    if status != 200:
        raise RuntimeError(_format_error(status, body, "/audio_query"))
    return json.loads(body.decode("utf-8"))


def synthesis(audio_query_json: dict, speaker_id: int, base_url: str = VOICEVOX_BASE) -> bytes:
    """
    根据 AudioQuery 合成语音，返回 WAV 字节。
    """
    url = f"{base_url}/synthesis?speaker={speaker_id}"
    data = json.dumps(audio_query_json, ensure_ascii=False).encode("utf-8")
    status, body = _request("POST", url, data=data)
    if status != 200:
        raise RuntimeError(_format_error(status, body, "/synthesis"))
    return body


def synthesize_simple(text: str, speaker_id: int, base_url: str = VOICEVOX_BASE) -> bytes:
    """
    简单合成：文本 -> WAV 字节。
    """
    if _use_core():
        from src.voice.voicevox_core_backend import synthesize_simple_core
        return synthesize_simple_core(text, speaker_id)
    q = audio_query(text, speaker_id, base_url)
    return synthesis(q, speaker_id, base_url)


# VOICEVOX 歌唱 API：frame_rate 通常为 93.75
SING_FRAME_RATE = 93.75
# sing_frame_audio_query 固定使用 6000，frame_synthesis 使用 /singers 中的 style_id
SING_FRAME_QUERY_SPEAKER = 6000


def sing_frame_audio_query(
    notes: list[dict],
    base_url: str = VOICEVOX_BASE,
) -> dict:
    """
    歌唱用帧级查询。speaker 固定为 6000，仅生成查询结构。
    notes: [{"id": str, "key": int, "frame_length": int, "lyric": str}, ...]
    """
    if _use_core():
        from src.voice.voicevox_core_backend import create_sing_frame_audio_query_core
        return create_sing_frame_audio_query_core(notes)
    url = f"{base_url}/sing_frame_audio_query?speaker={SING_FRAME_QUERY_SPEAKER}"
    data = json.dumps({"notes": notes}, ensure_ascii=False).encode("utf-8")
    status, body = _request("POST", url, data=data)
    if status != 200:
        raise RuntimeError(_format_error(status, body, "/sing_frame_audio_query"))
    return json.loads(body.decode("utf-8"))


def frame_synthesis(
    frame_audio_query: dict,
    speaker_id: int,
    base_url: str = VOICEVOX_BASE,
) -> bytes:
    """
    根据 FrameAudioQuery 合成歌唱 WAV 字节。
    speaker_id 必须使用 /singers 中的 style_id。
    """
    if _use_core():
        from src.voice.voicevox_core_backend import frame_synthesis_core
        return frame_synthesis_core(frame_audio_query, speaker_id)
    url = f"{base_url}/frame_synthesis?speaker={speaker_id}"
    data = json.dumps(frame_audio_query, ensure_ascii=False).encode("utf-8")
    status, body = _request("POST", url, data=data)
    if status != 200:
        raise RuntimeError(_format_error(status, body, "/frame_synthesis"))
    return body


_singers_cache: list[dict] | None = None


def clear_singers_cache() -> None:
    """刷新时清空歌唱角色缓存"""
    global _singers_cache
    _singers_cache = None


def fetch_singers(base_url: str = VOICEVOX_BASE) -> list[dict]:
    """
    获取歌唱用角色列表（/singers）。
    歌唱 API 需使用此列表中的 style_id，普通 /speakers 的 style 可能不支持歌唱。
    """
    from src.utils.voicevox_settings import get_voicevox_backend
    global _singers_cache
    if get_voicevox_backend() == "core" and not _is_core_available():
        return []  # 未就绪时返回空，音色面板仍可展示 TTS 列表
    if _singers_cache is not None:
        return _singers_cache
    if _use_core():
        from src.voice.voicevox_core_backend import get_singers_core
        _singers_cache = get_singers_core()
        return _singers_cache
    try:
        status, body = _request("GET", f"{base_url}/singers")
        if status != 200:
            _singers_cache = []
            return _singers_cache
        _singers_cache = json.loads(body.decode("utf-8")) or []
    except Exception:
        _singers_cache = []
    return _singers_cache


def get_singing_style_id(base_url: str = VOICEVOX_BASE) -> Optional[int]:
    """
    获取第一个可用的歌唱用 style_id。若无歌唱角色则返回 None。
    """
    try:
        singers = fetch_singers(base_url)
        for s in singers:
            for st in s.get("styles", []):
                return st.get("id")
    except Exception:
        pass
    return None


def is_singing_style(style_id: int, base_url: str = VOICEVOX_BASE) -> bool:
    """检查 style_id 是否支持歌唱（在 /singers 列表中）"""
    try:
        singers = fetch_singers(base_url)
        for s in singers:
            for st in s.get("styles", []):
                if st.get("id") == style_id:
                    return True
    except Exception:
        pass
    return False


def resolve_singing_style_id(
    style_id: int,
    base_url: str = VOICEVOX_BASE,
) -> Optional[int]:
    """
    将 /speakers 的 style_id 解析为歌唱 API 可用的 style_id。
    /singers 与 /speakers 的 style_id 可能不同（同一角色有说话用与歌唱用两种 ID），
    歌唱 API 必须使用 /singers 中的 style_id，否则会报「スタイルが見つかりませんでした」。
    若 style_id 已在 /singers 中则直接返回；否则按 speaker_uuid 匹配同角色的歌唱 style_id。
    """
    if is_singing_style(style_id, base_url):
        return style_id
    try:
        speakers = fetch_speakers(base_url)
        target_uuid: Optional[str] = None
        for sp in speakers:
            for st in sp.get("styles", []):
                if st.get("id") == style_id:
                    target_uuid = sp.get("speaker_uuid") or sp.get("uuid")
                    break
            if target_uuid:
                break
        if not target_uuid:
            return get_singing_style_id(base_url)
        singers = fetch_singers(base_url)
        target_uuid_str = str(target_uuid)
        for s in singers:
            u = s.get("speaker_uuid") or s.get("uuid")
            if u and str(u) == target_uuid_str:
                for st in s.get("styles", []):
                    sid = st.get("id")
                    if sid is not None:
                        return sid
                break
    except Exception:
        pass
    return get_singing_style_id(base_url)


def resolve_speakers_style_id(
    singer_style_id: int,
    base_url: str = VOICEVOX_BASE,
) -> Optional[int]:
    """
    将 /singers 的 style_id 解析为 TTS API 可用的 style_id。
    /audio_query 与 /synthesis 使用 /speakers 的 style_id。
    若 singer_style_id 已在 /speakers 中则直接返回；否则按 speaker_uuid + 风格索引匹配。
    """
    try:
        speakers = fetch_speakers(base_url)
        for sp in speakers:
            for st in sp.get("styles", []):
                if st.get("id") == singer_style_id:
                    return singer_style_id
        singers = fetch_singers(base_url)
        target_uuid: Optional[str] = None
        style_idx = -1
        for s in singers:
            for i, st in enumerate(s.get("styles", [])):
                if st.get("id") == singer_style_id:
                    target_uuid = s.get("speaker_uuid") or s.get("uuid")
                    style_idx = i
                    break
            if target_uuid is not None:
                break
        if not target_uuid or style_idx < 0:
            return None
        target_uuid_str = str(target_uuid)
        for sp in speakers:
            u = sp.get("speaker_uuid") or sp.get("uuid")
            if u and str(u) == target_uuid_str:
                styles = sp.get("styles", [])
                if style_idx < len(styles):
                    sid = styles[style_idx].get("id")
                    if sid is not None:
                        return sid
                break
    except Exception:
        pass
    return None


def get_legal_info_for_speaker(speaker_name: str) -> str:
    """根据音色名称返回利用規約信息。未配置时返回通用说明。"""
    for key, info in VOICE_LEGAL_INFO.items():
        if key in speaker_name or speaker_name in key:
            return info
    return f"""## {speaker_name}

该音色的利用規約请参考 VOICEVOX 引擎或音声库提供方的说明。

VOICEVOX: https://voicevox.huabyte.com/
voicevox_engine: https://github.com/VOICEVOX/voicevox_engine"""
