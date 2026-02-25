# -*- mode: python ; coding: utf-8 -*-
"""
ASCII Choir PyInstaller 打包配置
用法: pip install -r requirements.txt && pyinstaller ascii_choir.spec
"""
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_all

# Windows 上 numpy 需 collect_all 才能正确打包 DLL
numpy_datas, numpy_binaries, numpy_hidden = collect_all("numpy")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=numpy_binaries,
    # sound_library、workspaces 不打包，放 exe 同目录，避免每次启动解压
    datas=numpy_datas,
    hiddenimports=[
        "numpy", "Pillow",
        "sounddevice", "soundfile",
        "edge_tts", "audioread", "pydub", "audioop",
    ] + numpy_hidden + collect_submodules("PIL"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ASCII_Choir",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
