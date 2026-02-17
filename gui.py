"""
简谱演奏程序 GUI
"""
import re
import sys
import subprocess
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, scrolledtext, messagebox, filedialog
from pathlib import Path
import threading

from parser import parse
from player import Player
from validator import validate


def _mono_font(size: int = 12) -> tuple:
    """获取等宽字体，便于小节对齐（跨平台）"""
    for name in ("Consolas", "Menlo", "Monaco", "DejaVu Sans Mono", "Liberation Mono", "Courier New"):
        try:
            if name in tkfont.families():
                return (name, size)
        except tk.TclError:
            pass
    return ("Courier New", size)


def _is_dark_mode() -> bool:
    """检测系统是否处于夜间/深色模式（支持 Windows 和 macOS）"""
    try:
        if sys.platform == "darwin":
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            return result.returncode == 0 and "Dark" in result.stdout
        if sys.platform == "win32":
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                )
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return val == 0
            except (FileNotFoundError, OSError):
                return False
    except Exception:
        pass
    return False


# 示例简谱
SAMPLE_SCORE = r"""\tonality{1}
\beat{4/4}
\bpm{120}

|1 2 3 4|5 6 7 1.|1 2 3 4|5 4 3 2|1 - - -|
"""

SAMPLE_NO_BAR = r"""\no_bar_check
\bpm{80}

1 2 3 4 5 6 7 1.
"""
# \no_bar_check 时禁用小节号检查，beat 无效，适合自由记谱

SAMPLE_MULTI = r"""\tonality{1}
\beat{4/4}
\bpm{100}

& [8vb](|.3---|3---|.4---|4---|)
& |(1 2 5 1 2 5 1 2 5 1 2 5)3|8|8|8|

& [8vb](|.5---|5---|.6---|6- 5-|)
& |8|8|8|8|
"""


# 嵌套括号荧光色（浅色模式）
BRACKET_COLORS_LIGHT = ["#e8f4e8", "#e8e8f4", "#f4f4e8", "#f4e8f4", "#e8f4f4", "#f4e8e8"]
# 嵌套括号荧光色（深色模式）
BRACKET_COLORS_DARK = ["#1e3d2e", "#1e2e3d", "#3d3d1e", "#3d1e3d", "#1e3d3d", "#3d2e1e"]

# 应用根目录，用于预设工作区
APP_ROOT = Path(__file__).resolve().parent
WORKSPACES_DIR = APP_ROOT / "workspaces"
EXAMPLE_WORKSPACE = WORKSPACES_DIR / "示例"

# 示例工作区文件内容
EXAMPLE_FILES = {
    "单声部.choir": SAMPLE_SCORE,
    "无小节.choir": SAMPLE_NO_BAR,
    "多声部.choir": SAMPLE_MULTI,
}


def _ensure_example_workspace() -> Path:
    """确保示例工作区存在并写入示例文件"""
    EXAMPLE_WORKSPACE.mkdir(parents=True, exist_ok=True)
    for name, content in EXAMPLE_FILES.items():
        path = EXAMPLE_WORKSPACE / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    return EXAMPLE_WORKSPACE


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("简谱演奏 - ASCII Choir")
        self.root.geometry("1000x600")
        self.root.minsize(600, 400)
        
        self._dark_mode = _is_dark_mode()
        self.player = Player()
        self.play_thread: threading.Thread | None = None
        self.is_playing = False
        self.current_file_path: Path | None = None
        self.workspace_root: Path | None = None
        
        self._build_menu()
        self._build_ui()
    
    def _build_menu(self):
        """构建菜单栏：文件、编辑"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="打开...", command=self._on_open, accelerator="Ctrl+O")
        file_menu.add_command(label="保存", command=self._on_save, accelerator="Ctrl+S")
        file_menu.add_command(label="另存为...", command=self._on_save_as)
        file_menu.add_separator()
        file_menu.add_command(label="打开工作区...", command=self._on_open_workspace)
        file_menu.add_command(label="打开示例工作区", command=self._on_open_example_workspace)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.quit)
        
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="编辑", menu=edit_menu)
        edit_menu.add_command(label="格式化", command=self._on_format, accelerator="Ctrl+F")
        edit_menu.add_command(label="复制到剪贴板", command=self._on_copy, accelerator="Ctrl+Shift+C")
        
        self.root.bind("<Control-o>", lambda e: self._on_open())
        self.root.bind("<Control-s>", lambda e: self._on_save())
        self.root.bind("<Control-Shift-C>", lambda e: self._on_copy())
    
    def _on_open(self):
        path = filedialog.askopenfilename(
            title="打开简谱文件",
            filetypes=[("简谱文件", "*.choir *.txt"), ("所有文件", "*.*")],
        )
        if path:
            self._load_file(Path(path))
    
    def _on_save(self):
        if self.current_file_path:
            self._save_to(self.current_file_path)
        else:
            self._on_save_as()
    
    def _on_save_as(self):
        path = filedialog.asksaveasfilename(
            title="另存为",
            defaultextension=".choir",
            filetypes=[("简谱文件", "*.choir *.txt"), ("所有文件", "*.*")],
        )
        if path:
            self._save_to(Path(path))
    
    def _on_format(self):
        """格式化：对齐小节号"""
        self._on_align()
    
    def _on_copy(self):
        content = self.text.get(1.0, tk.END)
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.root.update()
        self.status_label.config(text="已复制到剪贴板")
        self.root.after(2000, lambda: self.status_label.config(text="就绪"))
    
    def _on_open_workspace(self):
        path = filedialog.askdirectory(title="选择工作区文件夹")
        if path:
            self._set_workspace(Path(path))
    
    def _on_open_example_workspace(self):
        """打开预设的示例工作区"""
        _ensure_example_workspace()
        self._set_workspace(EXAMPLE_WORKSPACE)
    
    def _load_file(self, path: Path):
        try:
            content = path.read_text(encoding="utf-8")
            self.text.delete(1.0, tk.END)
            self.text.insert(tk.END, content)
            self.current_file_path = path
            self.root.title(f"简谱演奏 - {path.name}")
            self._do_highlights()
        except Exception as e:
            messagebox.showerror("打开失败", str(e))
    
    def _save_to(self, path: Path, silent: bool = False):
        try:
            content = self.text.get(1.0, tk.END)
            path.write_text(content, encoding="utf-8")
            self.current_file_path = path
            self.root.title(f"简谱演奏 - {path.name}")
            if not silent:
                self.status_label.config(text="已保存")
                self.root.after(2000, lambda: self.status_label.config(text="就绪"))
            if self.workspace_root and path.parent == self.workspace_root:
                self._refresh_workspace_list()
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
    
    def _set_workspace(self, path: Path):
        self.workspace_root = path
        name = path.name if path else ""
        self._workspace_frame.config(text=f"工作区: {name}")
        self._refresh_workspace_list()
    
    def _refresh_workspace_list(self):
        """刷新左侧工作区文件列表"""
        self._workspace_list.delete(0, tk.END)
        if not self.workspace_root or not self.workspace_root.is_dir():
            return
        exts = {".choir", ".txt"}
        files = sorted(
            f for f in self.workspace_root.iterdir()
            if f.is_file() and f.suffix.lower() in exts
        )
        for f in files:
            self._workspace_list.insert(tk.END, f.name)
    
    def _on_workspace_file_select(self, event=None):
        sel = self._workspace_list.curselection()
        if not sel or not self.workspace_root:
            return
        idx = sel[0]
        files = sorted(
            f for f in self.workspace_root.iterdir()
            if f.is_file() and f.suffix.lower() in {".choir", ".txt"}
        )
        if idx < len(files):
            self._load_file(files[idx])
    
    def _build_ui(self):
        colors = self._theme_colors()
        if self._dark_mode:
            self.root.configure(bg="#2d2d2d")
        
        # 主容器：左侧工作区 + 右侧内容
        main_container = ttk.Frame(self.root, padding=5)
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # 左侧工作区面板
        ws_title = "工作区"
        self._workspace_frame = ttk.LabelFrame(main_container, text=ws_title, padding=5)
        self._workspace_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        ttk.Button(
            self._workspace_frame,
            text="打开工作区...",
            command=self._on_open_workspace,
        ).pack(fill=tk.X, pady=(0, 5))
        self._workspace_list = tk.Listbox(
            self._workspace_frame,
            width=18,
            height=20,
            font=_mono_font(10),
            selectmode=tk.SINGLE,
        )
        self._workspace_list.pack(fill=tk.BOTH, expand=True)
        self._workspace_list.bind("<Double-Button-1>", self._on_workspace_file_select)
        ws_scroll = ttk.Scrollbar(self._workspace_frame, orient=tk.VERTICAL, command=self._workspace_list.yview)
        self._workspace_list.configure(yscrollcommand=ws_scroll.set)
        ws_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 预设示例工作区
        _ensure_example_workspace()
        self._set_workspace(EXAMPLE_WORKSPACE)
        # 默认选中并加载第一个文件
        self._workspace_list.selection_set(0)
        first_file = EXAMPLE_WORKSPACE / "单声部.choir"
        if first_file.exists():
            self.current_file_path = first_file
            self.root.title(f"简谱演奏 - {first_file.name}")
        
        # 右侧主内容区
        main = ttk.Frame(main_container)
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 工具栏
        toolbar = ttk.Frame(main)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        
        self.btn_play = ttk.Button(toolbar, text="▶ 播放", command=self._on_play)
        self.btn_play.pack(side=tk.LEFT, padx=(0, 5))
        
        self.btn_stop = ttk.Button(toolbar, text="■ 停止", command=self._on_stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 5))
        
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        
        ttk.Button(toolbar, text="打开示例工作区", command=self._on_open_example_workspace).pack(side=tk.LEFT, padx=(0, 5))
        
        self.auto_wrap_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            toolbar,
            text="行超宽时 Ctrl+F 自动换新篇章",
            variable=self.auto_wrap_var,
        ).pack(side=tk.LEFT, padx=(15, 0))
        
        # 进度条
        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(main, variable=self.progress_var, maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 5))
        
        # 状态栏：行列号、总拍数、小节数
        self.status_frame = ttk.Frame(main)
        self.status_frame.pack(fill=tk.X)
        self.status_label = ttk.Label(self.status_frame, text="就绪")
        self.status_label.pack(side=tk.LEFT)
        self.status_bar = ttk.Label(self.status_frame, text="行: 1  列: 1  |  总拍: —  小节: —")
        self.status_bar.pack(side=tk.RIGHT)
        
        # 编辑区
        ttk.Label(main, text="简谱输入（支持 \\tonality、\\beat、\\bpm、\\no_bar_check）:").pack(anchor=tk.W)
        colors = self._theme_colors()
        # 等宽字体 + 不换行，便于 Ctrl+F 小节对齐
        self.text = scrolledtext.ScrolledText(
            main,
            wrap=tk.NONE,
            font=_mono_font(12),
            height=20,
            padx=8,
            pady=8,
            bg=colors["text_bg"],
            fg=colors["text_fg"],
            insertbackground=colors["text_fg"],
        )
        hscroll = tk.Scrollbar(main, orient=tk.HORIZONTAL, command=self.text.xview)
        self.text.configure(xscrollcommand=hscroll.set)
        self.text.pack(fill=tk.BOTH, expand=True, pady=5)
        hscroll.pack(fill=tk.X)
        # 默认加载示例工作区第一个文件
        first_file = EXAMPLE_WORKSPACE / "单声部.choir"
        if first_file.exists():
            self.text.insert(tk.END, first_file.read_text(encoding="utf-8"))
            self.current_file_path = first_file
        else:
            self.text.insert(tk.END, SAMPLE_SCORE)
        self.text.bind("<Control-f>", self._on_align)
        self.root.after(100, self._do_highlights)
        self.text.bind("<KeyRelease>", self._on_key_release)
        self.text.bind("<ButtonRelease-1>", self._on_cursor_move)

        # 可折叠的错误/警告面板
        self._diag_expanded = False
        self._diag_frame = ttk.Frame(main)
        self._diag_frame.pack(fill=tk.X, pady=(5, 0))
        self._diag_header = ttk.Frame(self._diag_frame)
        self._diag_header.pack(fill=tk.X)
        self._diag_toggle_btn = ttk.Button(
            self._diag_header,
            text="▶ 错误与警告 (0)",
            command=self._toggle_diagnostics,
        )
        self._diag_toggle_btn.pack(side=tk.LEFT)
        self._diag_content_frame = ttk.Frame(self._diag_frame)
        # 初始折叠，不 pack content
        self._diag_list = tk.Listbox(
            self._diag_content_frame,
            height=4,
            font=_mono_font(10),
            selectmode=tk.SINGLE,
        )
        self._diag_list.bind("<Double-Button-1>", self._on_diag_select)
        diag_scroll = ttk.Scrollbar(self._diag_content_frame, orient=tk.VERTICAL, command=self._diag_list.yview)
        self._diag_list.configure(yscrollcommand=diag_scroll.set)
        self._diag_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        diag_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        for i, c in enumerate(colors["bracket"]):
            self.text.tag_configure(f"bracket{i}", background=c)
        self.text.tag_configure("diag_error", underline=True, underlinefg="#c55")
        self.text.tag_configure("diag_warning", underline=True, underlinefg="#c9a227")
        self._bar_check_enabled = True  # \no_bar_check 时可关闭
        self._highlight_timer = None
        self._auto_save_timer = None
        
        self._update_diagnostics()

        # 底部说明（用 tk.Label 以支持主题色）
        hint_bg = "#2d2d2d" if self._dark_mode else "#f0f0f0"
        self.hint = tk.Label(
            main,
            text="支持: 1-7 音符, 0 休止, - 增加一拍, _ 缩短, . 八度, / 和弦, & 多声部, | 小节, ( )n n连音, ~ 连音线(可跨小节), # b ^ 升降还原, [xxx](...) 记号, [dc][fine] 反复 | Ctrl+F 对齐 | 括号高亮",
            font=("", 9),
            fg=colors["hint_fg"],
            bg=hint_bg,
        )
        self.hint.pack(anchor=tk.W)
    
    def _theme_colors(self) -> dict:
        """根据系统主题返回颜色配置"""
        if self._dark_mode:
            return {
                "text_bg": "#1e1e1e",
                "text_fg": "#d4d4d4",
                "hint_fg": "#808080",
                "bracket": BRACKET_COLORS_DARK,
            }
        return {
            "text_bg": "#ffffff",
            "text_fg": "#000000",
            "hint_fg": "gray",
            "bracket": BRACKET_COLORS_LIGHT,
        }
    
    def _load_sample(self, text: str):
        self.text.delete(1.0, tk.END)
        self.text.insert(tk.END, text)
    
    def _on_align(self, event=None):
        """Ctrl+F: 对齐小节号；勾选时行超宽则自动换新篇章"""
        content = self.text.get(1.0, tk.END)
        lines = content.split("\n")
        # 按双换行分篇章
        sections: list[list[tuple[int, str]]] = []
        current: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            if line.strip().startswith("&"):
                current.append((i, line))
            else:
                if current:
                    sections.append(current)
                    current = []
        if current:
            sections.append(current)
        
        if not sections:
            return "break"
        
        # 估算每行可显示字符数（等宽）
        try:
            cw = self.text.winfo_width() or 600
            f = tkfont.Font(self.text, self.text.cget("font"))
            char_w = max(f.measure("0"), 1)
            max_chars = max(60, int(cw / char_w) - 4)
        except Exception:
            max_chars = 80
        
        def simple_split(s: str) -> tuple[str, list[str]]:
            depth = 0
            parts = []
            cur = []
            for c in s:
                if c in "[(":
                    depth += 1
                    cur.append(c)
                elif c in "])":
                    depth -= 1
                    cur.append(c)
                elif c == "|" and depth == 0:
                    parts.append("".join(cur))
                    cur = []
                else:
                    cur.append(c)
            if cur:
                parts.append("".join(cur))
            prefix = parts[0].rstrip() if parts else ""
            bars = [p.strip() for p in parts[1:] if p.strip() and p.strip() != "8"]
            if bars and not parts[-1].strip():
                pass  # 结尾小节号 | 产生的空 bar 已忽略
            return prefix, bars
        
        def align_section(part_lines: list[tuple[int, str]]) -> list[str]:
            all_data = []
            for _, line in part_lines:
                prefix, bars = simple_split(line)
                all_data.append((prefix, bars))
            max_bars = max(len(bars) for _, bars in all_data)
            if max_bars == 0:
                return [L for _, L in part_lines]
            for prefix, bars in all_data:
                while len(bars) < max_bars:
                    bars.append("")
            col_widths = [max(max(len(bars[j]) for _, bars in all_data), 1) for j in range(max_bars)]
            result = []
            for (_, line), (prefix, bars) in zip(part_lines, all_data):
                if not bars and max_bars > 0:
                    result.append(line)
                    continue
                padded = [b.ljust(col_widths[j]) for j, b in enumerate(bars)]
                sep = " |" if prefix.rstrip().endswith("&") else "|"
                result.append(prefix.rstrip() + sep + "|".join(padded))
            return result
        
        new_sections: list[list[str]] = []
        for section in sections:
            part_lines = section
            if self.auto_wrap_var.get():
                any_long = any(len(L) > max_chars for _, L in part_lines)
                if any_long:
                    all_data = [(simple_split(L)) for _, L in part_lines]
                    n_bars = max(len(bars) for _, bars in all_data)
                    split_at = 1
                    for k in range(1, n_bars + 1):
                        ok = True
                        for prefix, bars in all_data:
                            seg = bars[:k]
                            test_len = len(prefix) + 2 + sum(len(b) + 1 for b in seg)
                            if test_len > max_chars:
                                ok = False
                                break
                        if ok:
                            split_at = k
                        else:
                            break
                    head_section = []
                    tail_section = []
                    for (prefix, bars) in all_data:
                        h = bars[:split_at] if len(bars) >= split_at else bars
                        t = bars[split_at:] if len(bars) > split_at else []
                        sep = " |" if prefix.rstrip().endswith("&") else "|"
                        if h:
                            head_section.append(prefix.rstrip() + sep + "|".join(h))
                        if t:
                            tail_section.append(prefix.rstrip() + sep + "|".join(t))
                    if head_section:
                        new_sections.append(align_section([(0, s) for s in head_section]))
                    if tail_section:
                        new_sections.append(align_section([(0, s) for s in tail_section]))
                    continue
            aligned = align_section(part_lines)
            new_sections.append(aligned)
        
        # 重建内容：每篇章内对齐，篇章间双换行，结尾小节号不输出
        output_blocks = []
        for block in new_sections:
            output_blocks.append("\n".join(block))
        new_content = "\n\n".join(output_blocks)
        header_lines = []
        for line in lines:
            if line.strip().startswith("&"):
                break
            header_lines.append(line)
        if header_lines:
            new_content = "\n".join(header_lines).rstrip() + "\n\n" + new_content
        self.text.delete(1.0, tk.END)
        self.text.insert(tk.END, new_content)
        return "break"
        self.text.delete(1.0, tk.END)
        self.text.insert(tk.END, "\n".join(out_lines))
        return "break"
    
    def _highlight_brackets(self):
        """括号捕获组高亮：嵌套的 [xxx](...) 使用不同荧光色"""
        for tag in self.text.tag_names():
            if tag.startswith("bracket"):
                self.text.tag_remove(tag, "1.0", tk.END)
        content = self.text.get(1.0, tk.END)
        spans: list[tuple[int, int]] = []
        i = 0
        while i < len(content):
            if content[i] == "[":
                depth, j = 0, i
                while j < len(content):
                    if content[j] == "[":
                        depth += 1
                    elif content[j] == "]":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                if j < len(content):
                    j += 1
                    while j < len(content) and content[j] in " \t\n":
                        j += 1
                    if j < len(content) and content[j] == "(":
                        depth, p = 0, j
                        while p < len(content):
                            if content[p] == "(":
                                depth += 1
                            elif content[p] == ")":
                                depth -= 1
                                if depth == 0:
                                    spans.append((i, p + 1))
                                    i = p
                                    break
                            p += 1
            i += 1
        # 计算每个 span 的嵌套层级（被多少外层包含）
        colors = self._theme_colors()["bracket"]
        for idx, (start, end) in enumerate(spans):
            level = sum(1 for a, b in spans if a < start and end < b)
            tag_idx = min(level, len(colors) - 1)
            self.text.tag_add(f"bracket{tag_idx}", f"1.0+{start}c", f"1.0+{end}c")
    
    def _toggle_diagnostics(self):
        """折叠/展开错误与警告面板"""
        self._diag_expanded = not self._diag_expanded
        if self._diag_expanded:
            self._diag_content_frame.pack(fill=tk.BOTH, expand=False)
        else:
            self._diag_content_frame.pack_forget()
        self._update_diag_header()

    def _update_diag_header(self):
        count = self._diag_list.size()
        arrow = "▼" if self._diag_expanded else "▶"
        self._diag_toggle_btn.config(text=f"{arrow} 错误与警告 ({count})")

    def _on_cursor_move(self, event=None):
        self._update_status_bar()

    def _update_status_bar(self, score=None):
        """更新状态栏：行列号、总拍数、小节数"""
        try:
            idx = self.text.index(tk.INSERT)
            line, col = map(int, idx.split("."))
        except Exception:
            line, col = 1, 1
        beats_str = "—"
        bars_str = "—"
        if score is None:
            score = getattr(self, "_cached_score", None)
        if score is None:
            try:
                score, _ = validate(self.text.get(1.0, tk.END))
            except Exception:
                score = None
        if score and score.parts:
            part0 = score.parts[0]
            total_beats = sum(
                ev.duration_beats
                for bar in part0.bars
                for ev in bar.events
                if hasattr(ev, "duration_beats")
            )
            bar_count = sum(len(s[0].bars) for s in score.sections) if score.sections else len(part0.bars)
            beats_str = f"{total_beats:.1f}"
            bars_str = str(bar_count)
        self.status_bar.config(text=f"行: {line}  列: {col}  |  总拍: {beats_str}  小节: {bars_str}")

    def _update_diagnostics(self):
        """根据当前文本更新诊断列表，并应用红/黄波浪线高亮"""
        try:
            content = self.text.get(1.0, tk.END)
            score, diags = validate(content)
        except Exception:
            score, diags = None, []
        self._cached_score = score
        self._diag_list.delete(0, tk.END)
        self._diag_data = diags
        self._update_status_bar(score)
        for d in diags:
            icon = "✕" if d.level == "error" else "⚠"
            self._diag_list.insert(tk.END, f"{icon} 第{d.line}行 第{d.column}列: {d.message}")
        self._update_diag_header()
        if diags and not self._diag_expanded:
            self._diag_expanded = True
            self._diag_content_frame.pack(fill=tk.BOTH, expand=False)
            self._update_diag_header()
        # 应用错误/警告波浪线（红色错误、黄色警告）
        self.text.tag_remove("diag_error", "1.0", tk.END)
        self.text.tag_remove("diag_warning", "1.0", tk.END)
        for d in diags:
            if d.start_pos is not None and d.end_pos is not None:
                tag = "diag_error" if d.level == "error" else "diag_warning"
                self.text.tag_add(tag, f"1.0+{d.start_pos}c", f"1.0+{d.end_pos}c")

    def _on_diag_select(self, event=None):
        """双击诊断项时跳转到对应行"""
        sel = self._diag_list.curselection()
        if not sel or not getattr(self, "_diag_data", None):
            return
        idx = sel[0]
        if idx < len(self._diag_data):
            d = self._diag_data[idx]
            self.text.see(f"{d.line}.{d.column}")
            self.text.mark_set(tk.INSERT, f"{d.line}.{d.column}")

    def _do_highlights(self):
        """执行括号高亮、诊断波浪线（错误红/警告黄）"""
        self._highlight_brackets()
        self._update_diagnostics()
    
    def _on_key_release(self, event=None):
        if self._highlight_timer:
            self.text.after_cancel(self._highlight_timer)
        self._highlight_timer = self.text.after(300, self._do_highlights)
        self.root.after(50, self._update_status_bar)
        self._schedule_auto_save()
    
    def _schedule_auto_save(self):
        """延迟 1.5 秒后自动保存"""
        if self._auto_save_timer:
            self.root.after_cancel(self._auto_save_timer)
        self._auto_save_timer = self.root.after(1500, self._do_auto_save)
    
    def _do_auto_save(self):
        """执行自动保存"""
        self._auto_save_timer = None
        if self.current_file_path:
            self._save_to(self.current_file_path, silent=True)
    
    def _on_play(self):
        if self.is_playing:
            return
        score = self.text.get(1.0, tk.END)
        if not score.strip():
            messagebox.showwarning("提示", "请输入简谱内容")
            return
        
        self.is_playing = True
        self.btn_play.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.status_label.config(text="播放中...")
        
        def progress_cb(current: float, total: float):
            if total > 0:
                pct = current / total * 100
                self.root.after(0, lambda: self.progress_var.set(pct))
        
        self.player.set_progress_callback(progress_cb)
        
        def run():
            try:
                self.player.play_score(score)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("播放错误", str(e)))
            finally:
                self.root.after(0, self._on_play_finished)
        
        self.play_thread = threading.Thread(target=run, daemon=True)
        self.play_thread.start()
    
    def _on_play_finished(self):
        self.is_playing = False
        self.btn_play.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.progress_var.set(100)
        self.status_label.config(text="播放完成")
    
    def _on_stop(self):
        self.player.stop()
        self.status_label.config(text="已停止")
    
    def run(self):
        self.root.mainloop()


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
