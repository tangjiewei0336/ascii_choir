"""打包后资源路径：开发时用项目根目录，打包后用 sys._MEIPASS"""
import sys
from pathlib import Path


def get_app_root() -> Path:
    """应用根目录（项目根或 PyInstaller 解压目录）"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent.parent
