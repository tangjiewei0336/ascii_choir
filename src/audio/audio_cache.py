"""
音频生成缓存：通过哈希判断是否已生成，避免重复调用 VOICEVOX/TTS 等耗时接口。
支持缓存大小限制（默认 500MB），超出后按访问时间逐出最久未用的项。
"""
import hashlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

if TYPE_CHECKING:
    from src.core.parser import ParsedScore

_DEFAULT_LIMIT_MB = 500
_SETTINGS_FILENAME = "cache_settings.json"


def _cache_dir() -> Path:
    """缓存目录：~/.cache/ascii_choir"""
    cache = Path.home() / ".cache" / "ascii_choir"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _config_dir() -> Path:
    """配置目录：与 GUI 的 _config_dir 一致"""
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Roaming" / "ASCII Choir"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ASCII Choir"
    return Path.home() / ".config" / "ascii_choir"


def _settings_path() -> Path:
    """设置文件路径"""
    return _config_dir() / _SETTINGS_FILENAME


def get_cache_size_limit_mb() -> float:
    """获取缓存大小限制（MB），默认 500"""
    p = _settings_path()
    if not p.exists():
        return float(_DEFAULT_LIMIT_MB)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return float(data.get("cache_limit_mb", _DEFAULT_LIMIT_MB))
    except Exception:
        return float(_DEFAULT_LIMIT_MB)


def set_cache_size_limit_mb(mb: float) -> None:
    """设置缓存大小限制（MB）"""
    p = _settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        data["cache_limit_mb"] = max(10.0, min(10000.0, float(mb)))
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_cache_size_bytes() -> int:
    """返回当前缓存总大小（字节）"""
    cache = _cache_dir()
    total = 0
    for f in cache.glob("*.npz"):
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def get_cache_size_mb() -> float:
    """返回当前缓存大小（MB）"""
    return get_cache_size_bytes() / (1024 * 1024)


def clear_cache() -> int:
    """清空所有缓存文件，返回删除的文件数"""
    cache = _cache_dir()
    count = 0
    for f in cache.glob("*.npz"):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count


def _touch_cache_file(cache_key: str) -> None:
    """更新缓存文件的访问时间（播放命中时调用）"""
    path = _cache_dir() / f"{cache_key}.npz"
    if path.exists():
        try:
            path.touch()
        except OSError:
            pass


def _evict_if_needed() -> None:
    """若缓存超出限制，按 mtime 从旧到新逐出"""
    limit_bytes = int(get_cache_size_limit_mb() * 1024 * 1024)
    cache = _cache_dir()
    files: list[tuple[Path, int, float]] = []
    total = 0
    for f in cache.glob("*.npz"):
        try:
            st = f.stat()
            size = st.st_size
            mtime = st.st_mtime
            files.append((f, size, mtime))
            total += size
        except OSError:
            pass
    if total <= limit_bytes:
        return
    # 按 mtime 升序（最旧在前），逐个删除直到 under limit
    files.sort(key=lambda x: x[2])
    for f, size, _ in files:
        if total <= limit_bytes:
            break
        try:
            f.unlink()
            total -= size
        except OSError:
            pass


def _make_hash(*args: Any) -> str:
    """根据参数生成 16 位哈希"""
    data = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def get_cached_audio(cache_key: str) -> Optional[tuple[np.ndarray, float]]:
    """
    从缓存加载音频。返回 (float32 数组, 时长秒) 或 None。
    命中时更新文件 mtime，便于按访问时间逐出。
    """
    path = _cache_dir() / f"{cache_key}.npz"
    if not path.exists():
        return None
    try:
        data = np.load(path, allow_pickle=False)
        audio = data["audio"]
        duration = float(data["duration"])
        _touch_cache_file(cache_key)
        return audio.astype(np.float32), duration
    except Exception:
        return None


def save_audio_to_cache(cache_key: str, audio: np.ndarray, duration: float) -> None:
    """将音频保存到缓存，超出限制时按 mtime 逐出旧项"""
    _evict_if_needed()
    path = _cache_dir() / f"{cache_key}.npz"
    try:
        np.savez_compressed(path, audio=audio.astype(np.float32), duration=duration)
        _evict_if_needed()
    except Exception:
        pass


def cache_key_play(score_text: str, sound_library_path: str, sample_rate: int) -> str:
    """全曲播放的缓存键"""
    return _make_hash("play", score_text, sound_library_path, sample_rate)


def cache_key_tts(text: str, lang: str, voice_id: Optional[int], sample_rate: int) -> str:
    """TTS 的缓存键"""
    return _make_hash("tts", text, lang, voice_id, sample_rate)


def _lyrics_fingerprint(score: "ParsedScore", section_index: int) -> list:
    """
    提取仅与人声轨相关的指纹，伴奏、其他声部、全局混响等变动不影响缓存。
    返回可序列化的结构，用于 cache_key_lyrics_from_parsed。
    """
    from src.core.parser import (
        ParsedScore,
        NoteEvent,
        ChordEvent,
        RestEvent,
        GlissEvent,
        TrillEvent,
    )

    sections = getattr(score, "sections", None) or []
    section_lyrics = getattr(score, "section_lyrics", None) or []
    section_settings = getattr(score, "section_settings", None) or []

    if section_index >= len(section_lyrics) or section_index >= len(sections):
        return []

    lyrics_part_indices: set[int] = set()
    all_sec_lyrics: list = []
    for si in range(section_index, len(section_lyrics)):
        sl = section_lyrics[si]
        all_sec_lyrics.append([(p, tuple(s), vid, mp, vol) for p, s, vid, mp, vol in sl])
        for p, s, vid, _, _ in sl:
            if vid is not None and s:
                lyrics_part_indices.add(p)
    if not lyrics_part_indices:
        return []

    fp: list = [("lyrics", all_sec_lyrics)]

    for sec_idx in range(section_index, len(sections)):
        section = sections[sec_idx]
        s = section_settings[sec_idx] if sec_idx < len(section_settings) else score.settings
        fp.append(("settings", (s.tonality, s.beat_numerator, s.beat_denominator, s.bpm, s.no_bar_check)))
        for part_idx in sorted(lyrics_part_indices):
            if part_idx >= len(section):
                continue
            part_bars = section[part_idx].bars
            bar_events: list = []
            for bar in part_bars:
                evs: list = []
                for ev in bar.events:
                    if isinstance(ev, NoteEvent):
                        evs.append(("N", ev.midi, ev.duration_beats, ev.lyric or ""))
                    elif isinstance(ev, ChordEvent):
                        evs.append(("C", tuple(ev.midis), ev.duration_beats, ev.lyric or ""))
                    elif isinstance(ev, RestEvent):
                        evs.append(("R", ev.duration_beats))
                    elif isinstance(ev, GlissEvent):
                        evs.append(("G", ev.start_midi, ev.end_midi, ev.duration_beats))
                    elif isinstance(ev, TrillEvent):
                        evs.append(("T", ev.main_midi, ev.duration_beats))
                bar_events.append(tuple(evs))
            fp.append(("part", part_idx, tuple(bar_events)))

    return fp


def cache_key_lyrics(score_text: str, section_index: int, sample_rate: int) -> str:
    """歌词歌声合成的缓存键（基于全文，兼容旧逻辑）"""
    return _make_hash("lyrics", score_text, section_index, sample_rate)


def cache_key_lyrics_from_parsed(score: "ParsedScore", section_index: int, sample_rate: int) -> str:
    """歌词歌声合成的缓存键（仅侦测人声轨变动，伴奏等修改不失效）"""
    fp = _lyrics_fingerprint(score, section_index)
    return _make_hash("lyrics_v2", fp, section_index, sample_rate)
