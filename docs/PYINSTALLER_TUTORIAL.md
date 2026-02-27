# PyInstaller 打包教程（含资源文件）

## 快速开始

```bash
pip install pyinstaller
pyinstaller ascii_choir.spec
./scripts/copy_resources_to_dist.sh
```

打包完成后，输出目录为 `dist/ASCII_Choir/`（onedir 模式，启动快）。**需执行 `copy_resources_to_dist.sh` 将 `sound_library`、`workspaces` 复制到该目录，否则运行时报错找不到资源。**

---

## 一、安装 PyInstaller

```bash
pip install pyinstaller
```

---

## 二、资源路径处理（已内置）

项目已包含 `src/utils/frozen_path.py`，并在以下模块中使用 `get_app_root()`：

- `src/audio/sound_loader.py`
- `src/instruments/instrument_registry.py`
- `src/ui/gui.py`
- `src/core/preprocessor.py`

打包后，`get_app_root()` 返回 exe 所在目录（`sound_library`、`workspaces` 放 exe 同目录，不打包进 exe 避免每次启动解压）。开发时返回项目根目录。

---

## 三、资源目录（不打包进 exe）

`sound_library`、`workspaces` **不打包进 exe**，而是放在 exe 同目录，避免每次启动解压耗时。

- 开发时：项目根目录下的 `sound_library`、`workspaces`
- 打包后：将 `sound_library`、`workspaces` 复制到 exe 同目录，`get_app_root() / "sound_library"` 即可访问

### 复制资源脚本

项目提供 `scripts/copy_resources_to_dist.sh`，打包后执行即可：

```bash
./scripts/copy_resources_to_dist.sh
```

或手动复制：

```bash
cp -r sound_library workspaces dist/ASCII_Choir/
```

Release 流程会在 zip 前自动复制；本地打包后需执行上述脚本或手动复制。

---

## 四、打包方式

### 方式 A：命令行（快速）

```bash
# 单文件 + 无控制台 + 打包资源（macOS/Linux）
pyinstaller --onefile --windowed \
  --add-data "sound_library:sound_library" \
  --add-data "workspaces:workspaces" \
  --name "ASCII_Choir" \
  main.py

# Windows 示例
pyinstaller --onefile --windowed ^
  --add-data "sound_library;sound_library" ^
  --add-data "workspaces;workspaces" ^
  --name "ASCII_Choir" ^
  main.py
```

### 方式 B：Spec 文件（推荐，可重复构建）

```bash
pyinstaller ascii_choir.spec
```

---

## 五、Spec 文件示例

创建 `ascii_choir.spec`：

```python
# -*- mode: python ; coding: utf-8 -*-
import sys

# Windows 用 ;，其他用 :
sep = ";" if sys.platform == "win32" else ":"

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("sound_library", "sound_library"),
        ("workspaces", "workspaces"),
    ],
    hiddenimports=[
        "numpy", "PIL", "sounddevice", "soundfile",
        "edge_tts", "audioread", "pydub",
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
    console=False,  # GUI 无控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

---

## 六、体积与资源取舍

| 策略 | 说明 | 体积 |
|------|------|------|
| 仅 grand_piano | 只打包 `sound_library/grand_piano` | 较小 |
| 全音色库 | 打包整个 `sound_library` | 较大（数百 MB） |
| 不含音色库 | 不打包，用户自行放置 | 最小 |

**仅打包 grand_piano 示例**：

```python
datas=[
    ("sound_library/grand_piano", "sound_library/grand_piano"),
    ("workspaces", "workspaces"),
],
```

其他乐器需用户将 `sound_library` 放在 exe 同目录，程序需支持「exe 同目录优先」的逻辑。

---

## 七、常见问题

### 1. 打包后运行报错 ModuleNotFoundError

在 spec 的 `hiddenimports` 中补充缺失模块。

### 2. 打包后找不到 sound_library

- 执行 `./scripts/copy_resources_to_dist.sh` 将资源复制到 dist/ASCII_Choir/
- 或手动 `cp -r sound_library workspaces dist/ASCII_Choir/`
- 运行 exe 时需在包含这两个目录的文件夹内，或从该文件夹启动

### 3. 启动慢

当前使用 **onedir** 模式，资源在目录中无需每次解压，启动较快。若需单文件分发，可改 spec 为 onefile，但启动会变慢。

### 4. 图标

```bash
--icon=icon.ico   # Windows
--icon=icon.icns  # macOS
```

---

## 八、完整流程示例

```bash
# 1. 打包
pyinstaller ascii_choir.spec

# 2. 复制资源到 dist/ASCII_Choir/（必须，否则运行时报错找不到 sound_library）
./scripts/copy_resources_to_dist.sh

# 3. 输出位置（onedir）
# dist/ASCII_Choir/ 目录，内含 ASCII_Choir.exe (Windows) 或 ASCII_Choir (macOS/Linux)
# sound_library/、workspaces/ 与可执行文件同目录
```

---

## 九、打 Windows 版本（在 macOS/Linux 上）

**PyInstaller 不支持跨平台编译**，必须在 Windows 上才能打出 .exe。

### 方式 A：有 Windows 电脑

在 Windows 上执行：

```cmd
pip install -r requirements.txt pyinstaller
pyinstaller ascii_choir.spec
```

输出为 `dist\ASCII_Choir\` 目录。随后执行资源复制（Git Bash 或 WSL）：

```bash
./scripts/copy_resources_to_dist.sh
```

或手动复制 `sound_library`、`workspaces` 到 `dist\ASCII_Choir\`。

### 方式 B：GitHub Actions 自动构建（推荐）

项目已包含 `.github/workflows/build-windows.yml`。把代码推到 GitHub 后：

1. 推送代码到 `main` 或 `dev` 分支（或手动触发 workflow）
2. 打开仓库 → **Actions** → 选择最新运行
3. 在 **Artifacts** 中下载 `ASCII_Choir-Windows`（含 exe 和资源）

无需本地 Windows 即可获得 Windows 版本。

### Windows 打包注意事项

- **输出**：`dist/ASCII_Choir/` 目录（onedir 模式，内含 exe 及依赖）
- **图标**：可在 spec 中加 `icon="icon.ico"` 指定 exe 图标
- **杀毒软件**：部分杀毒可能误报 PyInstaller 程序，可考虑代码签名（需证书）
