"""
完整音色目录与 speaker_uuid -> 所需 VVM 映射
用于本地库模式下显示全部音色，未下载的置灰并提示。
"""
import json
from pathlib import Path

from src.voice.voicevox_model_manager import is_vvm_installed

# speaker_uuid -> 角色名（用于音色面板显示）
SPEAKER_UUID_TO_NAME: dict[str, str] = {
    "7ffcb7ce-00ec-4bdc-82cd-45a8889e43ff": "四国めたん",
    "388f246b-8c41-4ac1-8e2d-5d79f3ff56d9": "ずんだもん",
    "35b2c544-660e-401e-b503-0e14c635303a": "春日部つむぎ",
    "3474ee95-c274-47f9-aa1a-8322163d96f1": "雨晴はう",
    "b1a81618-b27b-40d2-b0ea-27a9ad408c4b": "波音リツ",
    "c30dc15a-0992-4f8d-8bb8-ad3b314e6a6f": "玄野武宏",
    "e5020595-5c5d-4e87-b849-270a518d0dcf": "白上虎太郎",
    "4f51116a-d9ee-4516-925d-21f183e2afad": "青山龍星",
    "8eaad775-3119-417e-8cf4-2a10bfd592c8": "冥鳴ひまり",
    "481fb609-6446-4870-9f46-90c4dd623403": "九州そら",
    "9f3ee141-26ad-437e-97bd-d22298d02ad2": "もち子さん",
    "1a17ca16-7ee5-4ea5-b191-2f02ace24d21": "剣崎雌雄",
    "67d5d8da-acd7-4207-bb10-b5542d3a663b": "WhiteCUL",
    "0f56c2f2-644c-49c9-8989-94e11f7129d0": "後鬼",
    "044830d2-f23b-44d6-ac0d-b5d733caa900": "No.7",
    "468b8e94-9da4-4f7a-8715-a22a48844f9e": "ちび式じい",
    "0693554c-338e-4790-8982-b9c6d476dc69": "櫻歌ミコ",
    "a8cc6d22-aad0-4ab8-bf1e-2f843924164a": "小夜/SAYO",
    "882a636f-3bac-431a-966d-c5e6bba9f949": "ナースロボ＿タイプＴ",
    "471e39d2-fb11-4c8c-8d89-4b322d2498e0": "†聖騎士 紅桜†",
    "0acebdee-a4a5-4e12-a695-e19609728e30": "雀松朱司",
    "7d1e7ba7-f957-40e5-a3fc-da49f769ab65": "麒ヶ島宗麟",
    "ba5d2428-f7e0-4c20-ac41-9dd56e9178b4": "春歌ナナ",
    "00a5c10c-d3bd-459f-83fd-43180b521a44": "猫使アル",
    "c20a2254-0349-4470-9fc8-e5c0f8cf3404": "猫使ビィ",
    "1f18ffc3-47ea-4ce0-9829-0576d03a7ec8": "中国うさぎ",
    "04dbd989-32d0-40b4-9e71-17c920f2a8a9": "栗田まろん",
    "dda44ade-5f9c-4a3a-9d2c-2a976c7476d9": "あいえるたん",
    "287aa49f-e56b-4530-a469-855776c84a8d": "満別花丸",
    "97a4af4b-086e-4efd-b125-7ae2da85e697": "琴詠ニア",
    "0ebe2c7d-96f3-4f0e-a2e3-ae13fe27c403": "Voidoll",
    "0156da66-4300-474a-a398-49eb2e8dd853": "ぞん子",
    "4614a7de-9829-465d-9791-97eb8a5f9b86": "中部つるぎ",
    "3b91e034-e028-4acb-a08d-fbdcd207ea63": "離途",
    "0b466290-f9b6-4718-8d37-6c0c81e824ac": "黒沢冴白",
    "462cd6b4-c088-42b0-b357-3816e24f112e": "ユーレイちゃん",
    "80802b2d-8c75-4429-978b-515105017010": "東北ずん子",
    "1bd6b32b-d650-4072-bbe5-1d0ef4aaa28b": "東北きりたん",
    "ab4c31a3-8769-422a-b412-708f5ae637e8": "東北イタコ",
    "3be49e15-34bb-48a0-9e2f-9b80c96e9905": "あんこもん",
}

# speaker_uuid -> 该角色所需的 VVM：{"talk": "0.vvm", "sing": "s0.vvm"}
# 根据 VVM_CATALOG 的 desc 整理
SPEAKER_UUID_TO_VVM: dict[str, dict[str, str]] = {
    "7ffcb7ce-00ec-4bdc-82cd-45a8889e43ff": {"talk": "0.vvm", "sing": "s0.vvm"},   # 四国めたん
    "388f246b-8c41-4ac1-8e2d-5d79f3ff56d9": {"talk": "0.vvm", "sing": "s0.vvm"},   # ずんだもん
    "35b2c544-660e-401e-b503-0e14c635303a": {"talk": "0.vvm", "sing": "s0.vvm"},   # 春日部つむぎ
    "3474ee95-c274-47f9-aa1a-8322163d96f1": {"talk": "0.vvm", "sing": "s0.vvm"},   # 雨晴はう
    "b1a81618-b27b-40d2-b0ea-27a9ad408c4b": {"talk": "3.vvm", "sing": "s0.vvm"},   # 波音リツ
    "c30dc15a-0992-4f8d-8bb8-ad3b314e6a6f": {"talk": "4.vvm", "sing": "s0.vvm"},   # 玄野武宏
    "e5020595-5c5d-4e87-b849-270a518d0dcf": {"talk": "9.vvm", "sing": "s0.vvm"},   # 白上虎太郎
    "4f51116a-d9ee-4516-925d-21f183e2afad": {"talk": "15.vvm", "sing": "s0.vvm"},  # 青山龍星
    "8eaad775-3119-417e-8cf4-2a10bfd592c8": {"talk": "1.vvm", "sing": "s0.vvm"},   # 冥鳴ひまり
    "481fb609-6446-4870-9f46-90c4dd623403": {"talk": "2.vvm", "sing": "s0.vvm"},   # 九州そら
    "9f3ee141-26ad-437e-97bd-d22298d02ad2": {"talk": "15.vvm", "sing": "s0.vvm"},   # もち子さん
    "1a17ca16-7ee5-4ea5-b191-2f02ace24d21": {"talk": "4.vvm", "sing": "s0.vvm"},   # 剣崎雌雄
    "67d5d8da-acd7-4207-bb10-b5542d3a663b": {"talk": "8.vvm", "sing": "s0.vvm"},   # WhiteCUL
    "0f56c2f2-644c-49c9-8989-94e11f7129d0": {"talk": "7.vvm", "sing": "s0.vvm"},   # 後鬼
    "044830d2-f23b-44d6-ac0d-b5d733caa900": {"talk": "6.vvm", "sing": "s0.vvm"},   # No.7
    "468b8e94-9da4-4f7a-8715-a22a48844f9e": {"talk": "10.vvm", "sing": "s0.vvm"},  # ちび式じい
    "0693554c-338e-4790-8982-b9c6d476dc69": {"talk": "11.vvm", "sing": "s0.vvm"},   # 櫻歌ミコ
    "a8cc6d22-aad0-4ab8-bf1e-2f843924164a": {"talk": "15.vvm", "sing": "s0.vvm"},  # 小夜/SAYO
    "882a636f-3bac-431a-966d-c5e6bba9f949": {"talk": "11.vvm", "sing": "s0.vvm"},   # ナースロボ＿タイプＴ
    "471e39d2-fb11-4c8c-8d89-4b322d2498e0": {"talk": "12.vvm", "sing": "s0.vvm"},   # †聖騎士 紅桜†
    "0acebdee-a4a5-4e12-a695-e19609728e30": {"talk": "12.vvm", "sing": "s0.vvm"},   # 雀松朱司
    "7d1e7ba7-f957-40e5-a3fc-da49f769ab65": {"talk": "12.vvm", "sing": "s0.vvm"},   # 麒ヶ島宗麟
    "ba5d2428-f7e0-4c20-ac41-9dd56e9178b4": {"talk": "13.vvm", "sing": "s0.vvm"},   # 春歌ナナ
    "00a5c10c-d3bd-459f-83fd-43180b521a44": {"talk": "13.vvm", "sing": "s0.vvm"},   # 猫使アル
    "c20a2254-0349-4470-9fc8-e5c0f8cf3404": {"talk": "13.vvm", "sing": "s0.vvm"},   # 猫使ビィ
    "1f18ffc3-47ea-4ce0-9829-0576d03a7ec8": {"talk": "3.vvm", "sing": "s0.vvm"},   # 中国うさぎ
    "04dbd989-32d0-40b4-9e71-17c920f2a8a9": {"talk": "14.vvm", "sing": "s0.vvm"},   # 栗田まろん
    "dda44ade-5f9c-4a3a-9d2c-2a976c7476d9": {"talk": "14.vvm", "sing": "s0.vvm"},   # あいえるたん
    "287aa49f-e56b-4530-a469-855776c84a8d": {"talk": "14.vvm", "sing": "s0.vvm"},   # 満別花丸
    "97a4af4b-086e-4efd-b125-7ae2da85e697": {"talk": "14.vvm", "sing": "s0.vvm"},   # 琴詠ニア
    "0ebe2c7d-96f3-4f0e-a2e3-ae13fe27c403": {"talk": "17.vvm", "sing": "s0.vvm"},   # Voidoll
    "0156da66-4300-474a-a398-49eb2e8dd853": {"talk": "18.vvm", "sing": "s0.vvm"},   # ぞん子
    "4614a7de-9829-465d-9791-97eb8a5f9b86": {"talk": "18.vvm", "sing": "s0.vvm"},   # 中部つるぎ
    "3b91e034-e028-4acb-a08d-fbdcd207ea63": {"talk": "19.vvm", "sing": "s0.vvm"},   # 離途
    "0b466290-f9b6-4718-8d37-6c0c81e824ac": {"talk": "19.vvm", "sing": "s0.vvm"},   # 黒沢冴白
    "462cd6b4-c088-42b0-b357-3816e24f112e": {"talk": "20.vvm", "sing": "s0.vvm"},   # ユーレイちゃん
    "80802b2d-8c75-4429-978b-515105017010": {"talk": "21.vvm", "sing": "s0.vvm"},   # 東北ずん子
    "1bd6b32b-d650-4072-bbe5-1d0ef4aaa28b": {"talk": "21.vvm", "sing": "s0.vvm"},   # 東北きりたん
    "ab4c31a3-8769-422a-b412-708f5ae637e8": {"talk": "21.vvm", "sing": "s0.vvm"},   # 東北イタコ
    "3be49e15-34bb-48a0-9e2f-9b80c96e9905": {"talk": "22.vvm", "sing": "s0.vvm"},   # あんこもん
}


def get_required_vvm_for_speaker(speaker_uuid: str, for_talk: bool = True) -> str | None:
    """获取角色所需的 VVM 文件名。for_talk=True 为 TTS，False 为歌唱。"""
    m = SPEAKER_UUID_TO_VVM.get(str(speaker_uuid))
    if not m:
        return None
    return m.get("talk" if for_talk else "sing")


def is_speaker_available(speaker_uuid: str, for_talk: bool = True) -> bool:
    """角色是否已下载（所需 VVM 已安装）"""
    vvm = get_required_vvm_for_speaker(speaker_uuid, for_talk)
    return vvm is not None and is_vvm_installed(vvm)


_BUNDLED_SPEAKERS_PATH = Path(__file__).parent / "speakers_full_bundled.json"
_bundled_speakers_cache: list[dict] | None = None


def _load_bundled_speakers() -> list[dict]:
    """加载内嵌的完整音色列表"""
    global _bundled_speakers_cache
    if _bundled_speakers_cache is not None:
        return _bundled_speakers_cache
    if _BUNDLED_SPEAKERS_PATH.exists():
        try:
            _bundled_speakers_cache = json.loads(_BUNDLED_SPEAKERS_PATH.read_text(encoding="utf-8"))
            return _bundled_speakers_cache
        except Exception:
            pass
    _bundled_speakers_cache = []
    return _bundled_speakers_cache


def get_full_speakers_for_display() -> list[dict]:
    """
    获取完整音色列表（用于本地库模式无 VVM 或需展示未下载角色时）。
    优先使用 speakers_full_bundled.json，否则从 speaker_info_bundled 与 catalog 构建。
    """
    bundled = _load_bundled_speakers()
    if bundled:
        return bundled

    from src.voice.voicevox_speaker_cache import load_speaker_info_from_cache

    result: list[dict] = []
    for uuid_str in SPEAKER_UUID_TO_VVM:
        name = SPEAKER_UUID_TO_NAME.get(uuid_str, "?")
        info = load_speaker_info_from_cache(uuid_str)
        style_infos = (info or {}).get("style_infos") or []
        styles = []
        for si in style_infos:
            sid = si.get("id")
            sname = si.get("name", "通常")
            stype = si.get("type", "talk")
            if sid is not None:
                styles.append({"id": sid, "name": sname, "type": stype})
        if not styles:
            styles = [{"id": 0, "name": "通常", "type": "talk"}]
        result.append({"name": name, "speaker_uuid": uuid_str, "styles": styles})
    return result
