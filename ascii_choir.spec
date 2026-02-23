# -*- mode: python ; coding: utf-8 -*-
"""
ASCII Choir PyInstaller 打包配置
用法: pyinstaller ascii_choir.spec
"""
import sys

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("sound_library", "sound_library"),
        ("workspaces", "workspaces"),
    ],
    hiddenimports=[
        "numpy", "PIL", "PIL.Image", "PIL.ImageTk",
        "sounddevice", "soundfile",
        "edge_tts", "audioread", "pydub", "audioop",
    ],
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
