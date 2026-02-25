"""打包后资源路径：开发时用项目根目录，打包后用 exe 同目录（避免每次解压资源）"""
import sys
from pathlib import Path


def get_app_root() -> Path:
    """
    应用根目录。
    - 开发时：项目根目录
    - 打包后：exe 所在目录（sound_library、workspaces 放 exe 同目录，不打包进 exe 避免每次解压）
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent
