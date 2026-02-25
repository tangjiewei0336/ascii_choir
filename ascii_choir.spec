# -*- mode: python ; coding: utf-8 -*-
"""
ASCII Choir PyInstaller 打包配置
用法: pip install -r requirements.txt && pyinstaller ascii_choir.spec
"""
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_all

# Windows 上 numpy 需 collect_all 才能正确打包 DLL
numpy_datas, numpy_binaries, numpy_hidden = collect_all("numpy")

# voicevox_core 本地库（懒加载，需显式 collect）
try:
    vc_datas, vc_binaries, vc_hidden = collect_all("voicevox_core")
except Exception:
    vc_datas, vc_binaries, vc_hidden = [], [], []

# 音色头像等内嵌 JSON（Path(__file__).parent 会解析到 bundle 内）
voice_bundled = [
    ("src/voice/speaker_info_bundled.json", "src/voice"),
    ("src/voice/speakers_full_bundled.json", "src/voice"),
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=numpy_binaries + vc_binaries,
    # sound_library、workspaces 不打包，放 exe 同目录；音色头像 JSON、voicevox_core 需打包
    datas=numpy_datas + voice_bundled + vc_datas,
    hiddenimports=[
        "numpy", "Pillow",
        "sounddevice", "soundfile",
        "edge_tts", "audioread", "pydub", "audioop",
        "voicevox_core",
    ] + numpy_hidden + vc_hidden + collect_submodules("PIL"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

# onedir：启动快、可后续打包 voicevox_core，资源放 exe 同目录
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ASCII_Choir",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ASCII_Choir",
)
