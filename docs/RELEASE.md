# 发布 GitHub Release 指南

## 一、手动发布

### 1. 打标签

```bash
git tag v1.0.0
git push origin v1.0.0
```

### 2. 在 GitHub 创建 Release

1. 打开仓库 → **Releases** → **Draft a new release**
2. **Choose a tag**：选刚推送的 `v1.0.0`（或新建）
3. **Release title**：如 `v1.0.0`
4. **Describe**：写更新说明（可参考 CHANGELOG）
5. **Attach binaries**：拖入 `dist/ASCII_Choir`（macOS）或 `ASCII_Choir.exe`（Windows）
6. 点击 **Publish release**

### 3. 下载地址

发布后，用户可从 `https://github.com/用户名/ascii_choir/releases` 下载。

---

## 二、自动发布（推荐）

推送版本标签时，自动构建并创建 Release。

### 使用方式

```bash
# 1. 确认代码已提交
git add .
git commit -m "Release v1.0.0"
git push

# 2. 打标签并推送（触发自动发布）
git tag v1.0.0
git push origin v1.0.0
```

### 流程说明

- 推送 `v*` 标签（如 `v1.0.0`）后，GitHub Actions 会：
  1. 在 **macOS** 和 **Windows** 上分别打包
  2. 创建 GitHub Release
  3. 将构建产物上传为 Release 附件

- 在 **Releases** 页面可看到新版本及 `ASCII_Choir-macOS.zip`、`ASCII_Choir-Windows.zip` 等下载链接。
- 解压后需将 exe 与 `sound_library`、`workspaces` 置于同一目录运行（zip 内已包含）。
