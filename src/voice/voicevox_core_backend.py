"""
voicevox_core 本地库后端
不依赖 Docker/HTTP，直接调用 voicevox_core。
"""
import asyncio
import contextlib
import multiprocessing
import sys
from pathlib import Path
from typing import Optional


class _FilteredStderr:
    """过滤 CharacterVersion 版本差异警告，其余 stderr 正常输出。PyInstaller 下 sys.stderr 可能为 None。"""

    def __init__(self, original):
        self._original = original
        self._buf = ""

    def write(self, s: str) -> None:
        if self._original is None:
            return
        self._buf += s
        if "\n" in self._buf or "\r" in self._buf:
            for line in self._buf.splitlines(keepends=True):
                if "different `version`" in line and "CharacterVersion" in line:
                    continue
                self._original.write(line)
            self._buf = ""

    def flush(self) -> None:
        if self._original is None:
            self._buf = ""
            return
        if self._buf and "different `version`" not in self._buf:
            self._original.write(self._buf)
        self._buf = ""
        self._original.flush()

    def writable(self) -> bool:
        return True


@contextlib.contextmanager
def _suppress_version_warnings():
    """抑制 voicevox_core 加载 VVM 时的 CharacterVersion 版本差异警告（0.16.1 vs 0.16.0）。
    PyInstaller 下 sys.stderr 可能为 None，需跳过替换。"""
    orig = sys.stderr
    if orig is None:
        yield
        return
    filtered = _FilteredStderr(orig)
    sys.stderr = filtered
    try:
        yield
    finally:
        filtered.flush()
        sys.stderr = orig

from src.voice.voicevox_model_manager import (
    get_vvm_dir,
    get_open_jtalk_dict_dir,
    get_onnxruntime_dir,
    has_singing_model,
    has_talk_model,
)


def _run_async(coro):
    """在同步上下文中运行 async 函数"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return loop.run_until_complete(coro)


async def _init_synthesizer():
    """初始化 Synthesizer（需已加载 VVM）"""
    from voicevox_core.asyncio import Onnxruntime, OpenJtalk, Synthesizer

    onnx_dir = get_onnxruntime_dir()
    onnx_lib = None
    if onnx_dir.exists():
        for f in onnx_dir.iterdir():
            if "onnxruntime" in f.name.lower() or f.suffix in (".so", ".dll", ".dylib"):
                onnx_lib = str(f)
                break
        if not onnx_lib and (onnx_dir / "lib").exists():
            for f in (onnx_dir / "lib").iterdir():
                if "onnxruntime" in f.name.lower():
                    onnx_lib = str(f)
                    break

    if onnx_lib:
        ort = await Onnxruntime.load_once(filename=onnx_lib)
    else:
        ort = await Onnxruntime.load_once()

    dict_dir = get_open_jtalk_dict_dir()
    if not dict_dir.exists():
        raise FileNotFoundError(f"Open JTalk 辞書未找到: {dict_dir}")

    open_jtalk = await OpenJtalk.new(str(dict_dir))
    return Synthesizer(
        ort,
        open_jtalk,
        acceleration_mode="AUTO",
        cpu_num_threads=max(multiprocessing.cpu_count(), 2),
    )


# 全局 synthesizer 实例（懒加载）
_synthesizer = None
_loaded_vvms: set[str] = set()


def clear_loaded_vvm(vvm_path: str) -> None:
    """从加载缓存中移除已删除的 VVM 路径（删除后调用）"""
    global _loaded_vvms
    _loaded_vvms.discard(vvm_path)
    # 兼容不同路径格式
    norm = str(Path(vvm_path).resolve())
    _loaded_vvms.discard(norm)


def _ensure_synthesizer(vvm_path: Optional[Path] = None) -> "Synthesizer":
    """确保 synthesizer 已初始化并加载了至少一个 VVM"""
    global _synthesizer, _loaded_vvms

    if _synthesizer is None:
        _synthesizer = _run_async(_init_synthesizer())

    def _load_vvm(path: Path) -> None:
        key = str(path)
        if key in _loaded_vvms:
            return
        try:
            from voicevox_core.asyncio import VoiceModelFile
            with _suppress_version_warnings():
                model = _run_async(VoiceModelFile.open(str(path)))
                _run_async(_synthesizer.load_voice_model(model))
        except Exception as e:
            if "ModelAlreadyLoaded" in type(e).__name__ or "既に読み込まれています" in str(e):
                # 模型已加载，视为成功
                pass
            else:
                raise
        _loaded_vvms.add(key)

    if vvm_path and vvm_path.exists():
        _load_vvm(vvm_path)
    else:
        # 确保 s0.vvm 与 0.vvm 均已加载（若存在），使 TTS 与歌唱均可用
        vvm_dir = get_vvm_dir()
        for vvm in ["s0.vvm", "0.vvm"]:
            p = vvm_dir / vvm
            if p.exists():
                _load_vvm(p)

    return _synthesizer


def synthesize_simple_core(text: str, style_id: int) -> bytes:
    """简单 TTS 合成。若 style_id 未加载则尝试首个可用 talk 风格"""
    syn = _ensure_synthesizer()
    try:
        audio_query = _run_async(syn.create_audio_query(text, style_id))
        return _run_async(syn.synthesis(audio_query, style_id))
    except Exception as e:
        err_name = type(e).__name__
        if "StyleNotFound" in err_name or "スタイルが見つかりません" in str(e):
            # 回退到首个 talk 风格；若无则尝试任意风格
            for char in get_metas_core():
                for st in char.get("styles", []):
                    stype = st.get("type")
                    if stype == "talk" or (stype != "frame_decode" and stype != "sing"):
                        fallback_id = st.get("id")
                        if fallback_id is not None:
                            try:
                                audio_query = _run_async(syn.create_audio_query(text, fallback_id))
                                return _run_async(syn.synthesis(audio_query, fallback_id))
                            except Exception:
                                continue
            # 若仅有歌唱风格，提示需安装对话模型
            raise RuntimeError(
                "当前仅加载了歌唱模型（如 s0.vvm），TTS 试听需要对话模型。"
                "请在「音色 → VOICEVOX 音声模型管理」中下载 0.vvm 等对话模型。"
            ) from e
        raise


def frame_synthesis_core(frame_audio_query: dict, style_id: int) -> bytes:
    """歌唱合成。frame_audio_query 为 dict 格式（与 HTTP API 兼容）"""
    from voicevox_core.asyncio import Synthesizer

    syn = _ensure_synthesizer()
    # 将 dict 转为 voicevox_core 的 FrameAudioQuery
    faq = _dict_to_frame_audio_query(frame_audio_query)
    return _run_async(syn.frame_synthesis(faq, style_id))


def _dict_to_frame_audio_query(d: dict):
    """将 HTTP API 返回的 dict 转为 voicevox_core FrameAudioQuery"""
    from voicevox_core import FrameAudioQuery, FramePhoneme

    phonemes = []
    for p in d.get("phonemes", []):
        phonemes.append(FramePhoneme(
            phoneme=p.get("phoneme", ""),
            frame_length=p.get("frame_length", 0),
        ))
    return FrameAudioQuery(
        f0=d.get("f0", []),
        volume=d.get("volume", []),
        phonemes=phonemes,
        volume_scale=float(d.get("volume_scale", 1.0)),
        output_sampling_rate=int(d.get("output_sampling_rate", 24000)),
        output_stereo=bool(d.get("output_stereo", False)),
    )


def create_sing_frame_audio_query_core(notes: list[dict]) -> dict:
    """创建歌唱用 frame_audio_query。notes 格式与 HTTP API 一致。"""
    from voicevox_core import Note, Score

    score_notes = []
    for n in notes:
        lyric = n.get("lyric", "")
        key = n.get("key")
        fl = n.get("frame_length", 0)
        score_notes.append(Note(fl, lyric, key=key))

    score = Score(score_notes)
    syn = _ensure_synthesizer()
    # singing_teacher 固定 6000（波音リツ）
    faq = _run_async(syn.create_sing_frame_audio_query(score, 6000))
    # 转为 dict 以便后续处理
    return _frame_audio_query_to_dict(faq)


def _frame_audio_query_to_dict(faq) -> dict:
    """FrameAudioQuery 转 dict"""
    return {
        "f0": list(faq.f0),
        "volume": list(faq.volume),
        "phonemes": [{"phoneme": p.phoneme, "frame_length": p.frame_length} for p in faq.phonemes],
        "volume_scale": faq.volume_scale,
        "output_sampling_rate": faq.output_sampling_rate,
        "output_stereo": faq.output_stereo,
    }


def get_metas_core() -> list[dict]:
    """从已加载的模型中获取角色元信息（兼容 /speakers 格式）"""
    syn = _ensure_synthesizer()
    if syn is None:
        return []
    result = []
    for char in syn.metas():
        styles = []
        for s in char.styles:
            styles.append({"id": s.id, "name": s.name, "type": getattr(s, "type", None)})
        result.append({
            "name": char.name,
            "speaker_uuid": str(char.speaker_uuid),
            "styles": styles,
        })
    return result


def get_singers_core() -> list[dict]:
    """获取歌唱用角色（兼容 /singers 格式，仅含 frame_decode/sing 类型）"""
    all_metas = get_metas_core()
    result = []
    for char in all_metas:
        sing_styles = [s for s in char["styles"] if s.get("type") in ("frame_decode", "sing")]
        if sing_styles:
            result.append({
                "name": char["name"],
                "speaker_uuid": char["speaker_uuid"],
                "styles": sing_styles,
            })
    return result


def is_core_available() -> bool:
    """voicevox_core 是否可用"""
    try:
        from src.voice.voicevox_model_manager import is_core_ready
        return is_core_ready()
    except Exception:
        return False


def load_vvm_for_speaker(speaker_uuid: str, for_talk: bool = True) -> bool:
    """
    加载指定角色所需的 VVM。成功返回 True。
    """
    from src.voice.voicevox_speaker_catalog import get_required_vvm_for_speaker
    vvm_name = get_required_vvm_for_speaker(str(speaker_uuid), for_talk)
    if not vvm_name:
        return False
    vvm_dir = get_vvm_dir()
    vvm_path = vvm_dir / vvm_name
    if not vvm_path.exists():
        return False
    _ensure_synthesizer(vvm_path)
    return True


def resolve_style_id_for_speaker(speaker_uuid: str, for_talk: bool = True) -> Optional[int]:
    """
    获取角色的 style_id（需先 load_vvm_for_speaker）。返回第一个 talk 或 frame_decode/sing 风格的 id。
    """
    metas = get_metas_core()
    target_types = ("talk",) if for_talk else ("frame_decode", "sing")
    for char in metas:
        if str(char.get("speaker_uuid", "")) == str(speaker_uuid):
            for st in char.get("styles", []):
                if st.get("type") in target_types:
                    sid = st.get("id")
                    if sid is not None:
                        return sid
            # 若无精确匹配，取第一个风格
            for st in char.get("styles", []):
                sid = st.get("id")
                if sid is not None:
                    return sid
    return None
