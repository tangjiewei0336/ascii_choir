# 简谱演奏程序 - ASCII Choir

根据 readme.md 规范实现的简谱演奏程序，支持 GUI 与多声部同时演奏。

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
python main.py
```

## 支持功能

### 全局设定
- `\tonality{0}` / `\tonality{bA}` - 调性
- `\beat{4/4}` / `\beat{c}` - 拍号
- `\bpm{120}` - 速度
- `\no_bar_check` - 禁用小节号检查，beat 无效

### 音符与时值
- `1-7` 音符，`0` 休止，`0~` 休止至小节末
- `-` 增加一拍，`_` 缩短（÷2），支持 `|1 - - -|` 与 `|1---|`
- `.` 左低八度、右高八度
- `|` 小节，`/` 和弦
- `(1 2 3)3` n 连音
- `~5` 连音线（与前音合并，可连到上小节）
- `8` 重复上小节

### 升降号
- `#` 升号，`b` 降号，`^` 还原号

### [] 记号（括号可跨小节）
- 音量：`[fff]` `[f]` `[mp]` `[p]` 等
- 八度：`[8vb](...)` `[8va](...)` `[15va](...)`
- 和声：`[+3]` `[-3]` `[+5]` `[-5]`（按音名往下/上数，不可用于含升降号的音符）
- crescendo/decrescendo、deviation explicit
- `[dc]` Da Capo 跳回开头，`[fine]` 结束

### 多声部
- `&` 表示一条旋律，多行 `&` 同步演奏

### 编辑器
- **Ctrl+F** 自动对齐多声部小节号
- 括号捕获组淡色高亮

## 项目结构

```
ascii_choir/
├── main.py          # 入口
├── gui.py           # GUI 界面
├── parser.py        # 简谱解析
├── scheduler.py     # 事件调度、多声部对齐
├── player.py        # 音频播放
├── sound_loader.py  # 音色加载
└── sound_library/   # 音色库
```
