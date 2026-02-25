#!/usr/bin/env bash
# 将 sound_library、workspaces 复制到 dist/ASCII_Choir/（onedir 输出目录），供 exe 使用。
# 用法: 在项目根目录执行 ./scripts/copy_resources_to_dist.sh
# 或: bash scripts/copy_resources_to_dist.sh

set -e
cd "$(dirname "$0")/.."

DIST="dist"
OUTDIR="$DIST/ASCII_Choir"
if [[ ! -d "$DIST" ]]; then
  echo "错误: dist/ 目录不存在，请先执行 pyinstaller ascii_choir.spec 打包"
  exit 1
fi
if [[ ! -d "$OUTDIR" ]]; then
  echo "错误: $OUTDIR 不存在，请先执行 pyinstaller ascii_choir.spec 打包（onedir 模式）"
  exit 1
fi

echo "复制资源到 $OUTDIR/ ..."

if [[ -d "sound_library" ]]; then
  cp -R sound_library "$OUTDIR/"
  echo "  ✓ sound_library"
else
  echo "  ⚠ sound_library 不存在，跳过"
fi

if [[ -d "workspaces" ]]; then
  cp -R workspaces "$OUTDIR/"
  echo "  ✓ workspaces"
else
  echo "  ⚠ workspaces 不存在，跳过"
fi

echo "完成。可执行文件与资源已就绪于 $OUTDIR/"
