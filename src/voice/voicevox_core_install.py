"""
voicevox_core 平台相关安装
根据当前系统自动选择并安装对应 wheel。
"""
import platform
import subprocess
import sys
from typing import Optional

VOICEVOX_CORE_VERSION = "0.16.4"
RELEASE_BASE = f"https://github.com/VOICEVOX/voicevox_core/releases/download/{VOICEVOX_CORE_VERSION}"

# 平台 -> wheel 文件名
WHEEL_MAP = {
    ("Windows", "AMD64"): f"voicevox_core-{VOICEVOX_CORE_VERSION}-cp310-abi3-win_amd64.whl",
    ("Windows", "x86"): f"voicevox_core-{VOICEVOX_CORE_VERSION}-cp310-abi3-win32.whl",
    ("Darwin", "arm64"): f"voicevox_core-{VOICEVOX_CORE_VERSION}-cp310-abi3-macosx_11_0_arm64.whl",
    ("Darwin", "x86_64"): f"voicevox_core-{VOICEVOX_CORE_VERSION}-cp310-abi3-macosx_10_12_x86_64.whl",
    ("Linux", "x86_64"): f"voicevox_core-{VOICEVOX_CORE_VERSION}-cp310-abi3-manylinux_2_34_x86_64.whl",
    ("Linux", "aarch64"): f"voicevox_core-{VOICEVOX_CORE_VERSION}-cp310-abi3-manylinux_2_34_aarch64.whl",
}


def get_wheel_url() -> Optional[str]:
    """获取当前平台的 wheel 下载 URL，不支持则返回 None"""
    system = platform.system()
    machine = platform.machine()
    key = (system, machine)
    if key in WHEEL_MAP:
        return f"{RELEASE_BASE}/{WHEEL_MAP[key]}"
    # Linux aarch64 可能报告为 arm64
    if (system, "arm64") == ("Linux", machine):
        key = ("Linux", "aarch64")
        if key in WHEEL_MAP:
            return f"{RELEASE_BASE}/{WHEEL_MAP[key]}"
    return None


def is_voicevox_core_installed() -> bool:
    """检查 voicevox_core 是否已安装"""
    try:
        import voicevox_core  # noqa: F401
        return True
    except ImportError:
        return False


def install_voicevox_core(status_callback=None) -> tuple[bool, str]:
    """
    安装 voicevox_core。返回 (成功, 错误信息)
    """
    def _status(msg: str) -> None:
        if status_callback:
            try:
                status_callback(msg)
            except Exception:
                pass

    if is_voicevox_core_installed():
        return True, ""

    # 使用官方 GitHub wheel（与 requirements.txt 一致，无 deprecation 警告）
    url = get_wheel_url()
    if not url:
        return False, f"当前平台 ({platform.system()} {platform.machine()}) 暂无预编译 wheel，请使用 Docker 版 voicevox_engine 或手动: pip install -r requirements.txt"
    _status(f"正在从 GitHub 安装 voicevox_core...")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", url],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            return False, f"安装失败: {err}"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "安装超时，请检查网络后重试"
    except Exception as e:
        return False, str(e)
