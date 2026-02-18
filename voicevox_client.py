"""
VOICEVOX API 客户端
连接 voicevox_engine (http://localhost:50021)
支持 /speakers、/audio_query、/synthesis 端点
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

VOICEVOX_BASE = "http://127.0.0.1:50021"

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
    elif status in (0, -1) or "Connection" in body_str:
        msg += "\n\n请确认 voicevox_engine 已启动 (默认端口 50021)。"
    return msg


def fetch_speakers(base_url: str = VOICEVOX_BASE) -> list[dict]:
    """
    获取可用音色列表。
    返回 [{"name": "角色名", "speaker_uuid": "...", "styles": [{"id": 1, "name": "风格名"}, ...]}, ...]
    """
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
    """
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
    q = audio_query(text, speaker_id, base_url)
    return synthesis(q, speaker_id, base_url)


def get_legal_info_for_speaker(speaker_name: str) -> str:
    """根据音色名称返回利用規約信息。未配置时返回通用说明。"""
    for key, info in VOICE_LEGAL_INFO.items():
        if key in speaker_name or speaker_name in key:
            return info
    return f"""## {speaker_name}

该音色的利用規約请参考 VOICEVOX 引擎或音声库提供方的说明。

VOICEVOX: https://voicevox.huabyte.com/
voicevox_engine: https://github.com/VOICEVOX/voicevox_engine"""
