"""
VOICEVOX 音声模型管理
- 模型存储路径
- VVM 下载
- 依赖（ONNX Runtime、Open JTalk 辞書）检测与下载
"""
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from src.voice.voicevox_model_catalog import VVM_BASE, VVM_CATALOG, get_vvm_url


def _voicevox_data_dir() -> Path:
    """voicevox 数据目录（模型、onnx、辞書）"""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"
    return base / "ASCII_Choir" / "voicevox"


def get_vvm_dir() -> Path:
    """VVM 模型存储目录"""
    return _voicevox_data_dir() / "vvms"


def get_open_jtalk_dict_dir() -> Path:
    """Open JTalk 辞書目录"""
    return _voicevox_data_dir() / "dict" / "open_jtalk_dic_utf_8-1.11"


def get_onnxruntime_dir() -> Path:
    """ONNX Runtime 目录"""
    return _voicevox_data_dir() / "onnxruntime" / "lib"


def get_download_script_path() -> Path:
    """voicevox_core 官方 download 脚本路径"""
    return _voicevox_data_dir() / "download"


def is_vvm_installed(filename: str) -> bool:
    """检查指定 VVM 是否已下载"""
    return (get_vvm_dir() / filename).exists()


def get_installed_vvms() -> set[str]:
    """已安装的 VVM 文件名集合"""
    vvm_dir = get_vvm_dir()
    if not vvm_dir.exists():
        return set()
    return {f.name for f in vvm_dir.iterdir() if f.suffix.lower() == ".vvm"}


def delete_vvm(filename: str) -> tuple[bool, str]:
    """
    删除已下载的 VVM 文件。返回 (成功, 错误信息)
    """
    vvm_path = get_vvm_dir() / filename
    if not vvm_path.exists():
        return False, f"文件不存在: {filename}"
    try:
        vvm_path.unlink()
        # 通知 voicevox_core_backend 清除该模型的加载缓存
        try:
            from src.voice.voicevox_core_backend import clear_loaded_vvm
            clear_loaded_vvm(str(vvm_path))
        except Exception:
            pass
        return True, ""
    except OSError as e:
        return False, str(e)


def has_singing_model() -> bool:
    """是否有歌唱用模型（s0.vvm）"""
    return is_vvm_installed("s0.vvm")


def has_talk_model() -> bool:
    """是否有对话用模型（0.vvm 或任意 talk 用 vvm）"""
    return is_vvm_installed("0.vvm") or any(is_vvm_installed(e.filename) for e in VVM_CATALOG if not e.supports_sing)


def is_core_ready() -> bool:
    """voicevox_core 是否可用（库已装 + 依赖齐全）"""
    try:
        import voicevox_core  # noqa: F401
    except ImportError:
        return False
    dict_dir = get_open_jtalk_dict_dir()
    if not dict_dir.exists() or not (dict_dir / "sys.dic").exists():
        return False
    # ONNX 可选，voicevox_core 可能自带或从系统加载
    return True


def download_vvm(
    filename: str,
    status_callback: Optional[Callable[[str], None]] = None,
) -> tuple[bool, str]:
    """
    下载单个 VVM 文件。返回 (成功, 错误信息)
    """
    def _status(msg: str) -> None:
        if status_callback:
            try:
                status_callback(msg)
            except Exception:
                pass

    url = get_vvm_url(filename)
    dest_dir = get_vvm_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename

    _status(f"正在下载 {filename}...")
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=300) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 256
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total and status_callback:
                        pct = int(100 * downloaded / total)
                        _status(f"正在下载 {filename}... {pct}%")
        return True, ""
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"网络错误: {e.reason}"
    except Exception as e:
        if dest.exists():
            dest.unlink()
        return False, str(e)


def run_download_script(status_callback: Optional[Callable[[str], None]] = None) -> tuple[bool, str]:
    """
    下载并运行 voicevox_core 官方 download 脚本，获取 ONNX Runtime 和 Open JTalk 辞書。
    返回 (成功, 错误信息)
    """
    def _status(msg: str) -> None:
        if status_callback:
            try:
                status_callback(msg)
            except Exception:
                pass

    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        system = "osx"
    elif system == "Windows":
        system = "windows"
    elif system == "Linux":
        system = "linux"
    else:
        return False, f"不支持的系统: {system}"

    if "arm" in machine or machine == "aarch64":
        arch = "arm64"
    else:
        arch = "x64"

    script_name = f"download-{system}-{arch}"
    if system == "windows":
        script_name += ".exe"

    data_dir = _voicevox_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    script_path = data_dir / ("download.exe" if system == "windows" else "download")

    # 下载脚本
    url = f"https://github.com/VOICEVOX/voicevox_core/releases/latest/download/{script_name}"
    _status("正在下载 voicevox 依赖安装脚本...")
    try:
        urllib.request.urlretrieve(url, script_path)
    except Exception as e:
        return False, f"下载脚本失败: {e}"

    if system != "windows":
        script_path.chmod(0o755)

    # 运行脚本，输出到 data_dir（自动传入 y 同意 VOICEVOX 音声モデル / ONNX Runtime 利用規約）
    _status("正在安装 ONNX Runtime 和 Open JTalk 辞書...（已自动同意利用规约）")
    try:
        proc = subprocess.Popen(
            [str(script_path), "-o", str(data_dir), "--exclude", "c-api"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(data_dir),
        )
        # 脚本会询问两次同意（音声モデル、ONNX Runtime），预先传入 y
        out, _ = proc.communicate(input="y\ny\n", timeout=600)
        out_lines = (out or "").splitlines()
        for line in out_lines[-10:]:
            if line.strip() and status_callback:
                _status(line[:80])
        if proc.returncode != 0:
            return False, "\n".join(out_lines[-5:]) or "安装脚本执行失败"
    except Exception as e:
        return False, str(e)
    finally:
        if script_path.exists():
            script_path.unlink(missing_ok=True)

    # 检查结果
    dict_dir = get_open_jtalk_dict_dir()
    if not dict_dir.exists():
        return False, "辞書未正确安装，请手动运行 voicevox_core 的 download 脚本"

    return True, ""
