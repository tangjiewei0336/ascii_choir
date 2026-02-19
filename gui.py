"""
简谱演奏程序 GUI
"""
import re
import sys
import subprocess
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, scrolledtext, messagebox, filedialog, simpledialog
from pathlib import Path
import threading

from chord_utils import (
    chord_sort,
    chord_swap_two,
    duration_divide_two,
    duration_multiply_two,
    find_note_tokens_in_range,
    get_chords_to_operate,
    get_tonality_offset,
)
from parser import parse
from player import Player
from preprocessor import expand_imports
from renderer import render_to_image, render_to_pil
from validator import validate, VOICEVOX_UNREACHABLE_MSG
from breakpoints import load_breakpoints, save_breakpoints, rename_breakpoints
from voicevox_client import VOICEVOX_BASE


def show_error_detail(parent: tk.Tk, title: str, message: str, traceback_str: str | None = None) -> None:
    """展示详细错误信息，含堆栈或较长时用可滚动对话框"""
    full = message
    if traceback_str:
        full = f"{message}\n\n--- 详细堆栈 ---\n{traceback_str}"
    if not traceback_str and len(message) < 200 and message.count("\n") < 2:
        messagebox.showerror(title, message, parent=parent)
        return
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.transient(parent)
    dlg.geometry("520x360")
    ttk.Label(dlg, text=message.split("\n")[0][:80] + ("..." if len(message.split("\n")[0]) > 80 else ""), wraplength=480).pack(anchor=tk.W, padx=10, pady=(10, 5))
    txt = scrolledtext.ScrolledText(dlg, wrap=tk.WORD, font=("Consolas", 10), height=14, width=60)
    txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
    txt.insert(tk.END, full)
    txt.config(state=tk.DISABLED)
    ttk.Button(dlg, text="关闭", command=dlg.destroy).pack(pady=(0, 10))
    dlg.geometry(f"+{parent.winfo_rootx() + 80}+{parent.winfo_rooty() + 120}")


def _ask_rename(prompt: str, initial: str, parent: tk.Tk) -> str | None:
    """重命名对话框：只选中文件名部分，不选中后缀"""
    result: list[str | None] = [None]

    def on_ok():
        result[0] = entry.get().strip() or None
        dlg.destroy()

    def on_cancel():
        dlg.destroy()

    dlg = tk.Toplevel(parent)
    dlg.title("重命名")
    dlg.transient(parent)
    dlg.grab_set()
    ttk.Label(dlg, text=prompt).pack(anchor=tk.W, padx=10, pady=(10, 0))
    entry = ttk.Entry(dlg, width=40)
    entry.pack(padx=10, pady=5, fill=tk.X)
    entry.insert(0, initial)
    entry.focus_set()

    def _select_stem():
        stem_len = len(initial)
        if initial.endswith(".choir"):
            stem_len = len(initial) - 6
        elif initial.endswith(".txt"):
            stem_len = len(initial) - 4
        entry.selection_range(0, stem_len)
        entry.icursor(stem_len)

    dlg.after(50, _select_stem)

    btn_frame = ttk.Frame(dlg)
    btn_frame.pack(pady=(5, 10))
    ttk.Button(btn_frame, text="确定", command=on_ok).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="取消", command=on_cancel).pack(side=tk.LEFT)
    entry.bind("<Return>", lambda e: on_ok())
    entry.bind("<Escape>", lambda e: on_cancel())
    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
    dlg.geometry(f"+{parent.winfo_rootx() + 50}+{parent.winfo_rooty() + 100}")
    dlg.wait_window()
    return result[0]


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


DEFAULT_NEW_FILE = r"""\tonality{0}
\beat{4/4}
\bpm{120}

"""

# 示例简谱
SAMPLE_SCORE = r"""\tonality{0}
\beat{4/4}
\bpm{120}

|1 2 3 4|5 6 7 1.|1 2 3 4|5 4 3 2|1 - - -|
"""

SAMPLE_NO_BAR = r"""\no_bar_check
\bpm{80}

1 2 3 4 5 6 7 1.
"""
# \no_bar_check 时禁用小节号检查，beat 无效，适合自由记谱

SAMPLE_MULTI = r"""\tonality{0}
\beat{4/4}
\bpm{30}

& [8vb](|.3---|3---|.4---|4---|)
& |(1 2 5 1 2 5 1 2 5 1 2 5)3|8|8|8|

& [8vb](|.5---|5---|[fine].6---|6- 5-[dc]|)
& |8|8|8|8|
"""

SAMPLE_KEY_CHANGE = r"""\tonality{C}
\beat{4/4}
\bpm{60}

|1 2 3 2|5 4 3 2|1 - - -|0 - - - |

\tonality{#C}

|1 2 3 4|5 4 3 2|1 - - -|0 - - - |

\tonality{D}

|1 2 3 4|5 4 3 2|1 - - -|0 - - - |

\tonality{#D}

|1 2 3 4|5 4 3 2|1 - - -|0 - - - |

\tonality{E}

|1 2 3 4|5 4 3 2|1 - - -|0 - - - |
"""

SAMPLE_AUTO_HARMONY = r"""\tonality{0}
\beat{4/4}
\bpm{60}

|0 - 0 [-3]((1 2)_ | 3 (4 5)_ 0_ 5 5_ | ~5 3 5 1. | 7 1._ 7_ ~7_ 5 5_ | ~5 -) 0 3_ 2_|
|1 0 (.5/1 .7/2 1/3 2/4)_| ~2/4 1/3 .7/2 (.5/1 .5/1)_| ~.5/1 - - -|
"""

SAMPLE_HARMONY = r"""\tonality{0}
\beat{4/4}
\bpm{60}

|0 - 0 (.6/1 .7/2)_ | 1/3 (2/4 3/5 0)_  3/5 3/5_ | ~3/~5 1/3 3/5 6/1. |
"""


# 嵌套括号荧光色（浅色模式）
BRACKET_COLORS_LIGHT = ["#e8f4e8", "#e8e8f4", "#f4f4e8", "#f4e8f4", "#e8f4f4", "#f4e8e8"]
# 嵌套括号荧光色（深色模式）
BRACKET_COLORS_DARK = ["#1e3d2e", "#1e2e3d", "#3d3d1e", "#3d1e3d", "#1e3d3d", "#3d2e1e"]

# 应用根目录，用于预设工作区
APP_ROOT = Path(__file__).resolve().parent
WORKSPACES_DIR = APP_ROOT / "workspaces"
EXAMPLE_WORKSPACE = WORKSPACES_DIR / "示例"

# 记住上次工作区的配置路径
def _config_dir() -> Path:
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Roaming" / "ASCII Choir"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ASCII Choir"
    return Path.home() / ".config" / "ascii_choir"


def _load_last_workspace() -> Path | None:
    """读取上次打开的工作区路径"""
    cfg = _config_dir() / "last_workspace.txt"
    if not cfg.exists():
        return None
    try:
        path = Path(cfg.read_text(encoding="utf-8").strip())
        return path if path.is_dir() else None
    except Exception:
        return None


def _save_last_workspace(path: Path) -> None:
    """保存工作区路径供下次启动使用"""
    cfg = _config_dir() / "last_workspace.txt"
    try:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(str(path.resolve()), encoding="utf-8")
    except Exception:
        pass

# 示例工作区文件内容
EXAMPLE_FILES = {
    "单声部.choir": SAMPLE_SCORE,
    "无小节.choir": SAMPLE_NO_BAR,
    "多声部.choir": SAMPLE_MULTI,
    "变调.choir": SAMPLE_KEY_CHANGE,
    "自动和声.choir": SAMPLE_AUTO_HARMONY,
    "和声.choir": SAMPLE_HARMONY,
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
        self.root.geometry("1280x720")
        self.root.minsize(700, 500)
        
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
        file_menu.add_command(label="新建", command=self._on_new, accelerator="Ctrl+N")
        file_menu.add_command(label="打开...", command=self._on_open, accelerator="Ctrl+O")
        file_menu.add_command(label="保存", command=self._on_save, accelerator="Ctrl+S")
        file_menu.add_command(label="另存为...", command=self._on_save_as)
        file_menu.add_command(label="导出带歌词简谱 (JPG)...", command=self._on_export_lyrics_jpg)
        file_menu.add_separator()
        file_menu.add_command(label="打开工作区...", command=self._on_open_workspace)
        file_menu.add_command(label="打开示例工作区", command=self._on_open_example_workspace)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.quit)
        
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="编辑", menu=edit_menu)
        edit_menu.add_command(label="剪切", command=self._on_cut, accelerator="Ctrl+X")
        edit_menu.add_command(label="复制", command=self._on_copy_selection, accelerator="Ctrl+C")
        edit_menu.add_command(label="粘贴", command=self._on_paste, accelerator="Ctrl+V")
        edit_menu.add_command(label="全选", command=self._on_select_all, accelerator="Ctrl+A")
        edit_menu.add_separator()
        edit_menu.add_command(label="格式化", command=self._on_format, accelerator="Ctrl+F")
        edit_menu.add_command(label="复制全部到剪贴板", command=self._on_copy_all, accelerator="Ctrl+Shift+C")

        tts_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="TTS", menu=tts_menu)
        tts_menu.add_command(label="VOICEVOX 音色选择...", command=self._on_voicevox_voices)
        
        self.root.bind("<Control-n>", lambda e: self._on_new())
        self.root.bind("<Control-o>", lambda e: self._on_open())
        self.root.bind("<Control-s>", lambda e: self._on_save())
        self.root.bind("<Control-Shift-C>", lambda e: self._on_copy_all())
    
    def _on_new(self):
        """新建文件：先询问名称，然后直接保存"""
        name = simpledialog.askstring("新建", "请输入文件名：", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        if not name.endswith(".choir") and not name.endswith(".txt"):
            name += ".choir"
        if self._auto_save_timer:
            self.root.after_cancel(self._auto_save_timer)
            self._auto_save_timer = None
        self.text.delete(1.0, tk.END)
        self.text.insert(tk.END, DEFAULT_NEW_FILE)
        if self.workspace_root and self.workspace_root.is_dir():
            path = self.workspace_root / name
        else:
            path = filedialog.asksaveasfilename(
                title="保存新建文件",
                initialfile=name,
                defaultextension=".choir",
                filetypes=[("简谱文件", "*.choir *.txt"), ("所有文件", "*.*")],
            )
            if not path:
                return
            path = Path(path)
        self._save_to(path)
        self._do_highlights()
    
    def _on_open(self):
        initialdir = str(self.current_file_path.parent) if self.current_file_path else None
        path = filedialog.askopenfilename(
            title="打开简谱文件",
            initialdir=initialdir,
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
        initialdir = str(self.current_file_path.parent) if self.current_file_path else None
        initialfile = self.current_file_path.name if self.current_file_path else None
        path = filedialog.asksaveasfilename(
            title="另存为",
            initialdir=initialdir,
            initialfile=initialfile,
            defaultextension=".choir",
            filetypes=[("简谱文件", "*.choir *.txt"), ("所有文件", "*.*")],
        )
        if path:
            self._save_to(Path(path))

    def _on_export_lyrics_jpg(self):
        """导出带歌词简谱为 JPG"""
        content = self.text.get(1.0, tk.END)
        if not content.strip():
            messagebox.showwarning("提示", "请输入简谱内容")
            return
        base_dir = (
            self.workspace_root
            if self.workspace_root and self.workspace_root.is_dir()
            else (self.current_file_path.parent if self.current_file_path else Path.cwd())
        )
        try:
            content = expand_imports(content, base_dir)
        except (FileNotFoundError, ValueError, OSError) as e:
            import traceback
            show_error_detail(self.root, "导入错误", str(e), traceback.format_exc())
            return
        try:
            score = parse(content)
        except Exception as e:
            import traceback
            show_error_detail(self.root, "解析错误", str(e), traceback.format_exc())
            return
        initialdir = str(self.current_file_path.parent) if self.current_file_path else None
        initialfile = (self.current_file_path.stem + ".jpg") if self.current_file_path else None
        path = filedialog.asksaveasfilename(
            title="导出带歌词简谱",
            initialdir=initialdir,
            initialfile=initialfile,
            defaultextension=".jpg",
            filetypes=[("JPEG 图片", "*.jpg *.jpeg"), ("所有文件", "*.*")],
        )
        if not path:
            return
        layout = simpledialog.askstring("布局", "输入布局: vertical(上下) 或 horizontal(左右)，直接回车默认 vertical", initialvalue="vertical")
        layout = (layout or "vertical").strip().lower() or "vertical"
        if layout not in ("vertical", "horizontal"):
            layout = "vertical"
        try:
            out = render_to_image(score, path, layout=layout)
            messagebox.showinfo("导出成功", f"已保存到 {out}")
        except ImportError as e:
            import traceback
            show_error_detail(self.root, "导出失败", "请安装 Pillow: pip install Pillow", traceback.format_exc())
        except Exception as e:
            import traceback
            show_error_detail(self.root, "导出失败", str(e), traceback.format_exc())

    def _on_format(self):
        """格式化：对齐小节号"""
        self._on_align()
    
    def _on_cut(self):
        """剪切选中内容到剪贴板"""
        try:
            sel = self.text.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.root.clipboard_clear()
            self.root.clipboard_append(sel)
            self.root.update()
            self.text.delete(tk.SEL_FIRST, tk.SEL_LAST)
            self._schedule_auto_save()
            self._schedule_preview()
        except tk.TclError:
            pass  # 无选中内容

    def _on_copy_selection(self):
        """复制选中内容到剪贴板"""
        try:
            sel = self.text.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.root.clipboard_clear()
            self.root.clipboard_append(sel)
            self.root.update()
            self.status_label.config(text="已复制")
            self.root.after(2000, lambda: self.status_label.config(text="就绪"))
        except tk.TclError:
            pass  # 无选中内容

    def _on_paste(self):
        """从剪贴板粘贴到光标位置"""
        try:
            content = self.root.clipboard_get()
            self.text.insert(tk.INSERT, content)
            self._schedule_auto_save()
            self._schedule_preview()
        except tk.TclError:
            pass  # 剪贴板为空或不可用

    def _on_select_all(self):
        """全选编辑器内容"""
        self.text.tag_add(tk.SEL, "1.0", tk.END)
        self.text.mark_set(tk.INSERT, "1.0")
        self.text.see(tk.INSERT)

    def _on_text_context_menu(self, event):
        """编辑器右键菜单：剪切、复制、粘贴、全选；若光标在和弦上则显示和弦操作"""
        self.text.focus_set()
        idx = self.text.index(f"@{event.x},{event.y}")
        content = self.text.get(1.0, tk.END)
        cursor_pos = len(self.text.get(1.0, idx))
        try:
            sel_first = self.text.index(tk.SEL_FIRST)
            sel_last = self.text.index(tk.SEL_LAST)
            sel_start = len(self.text.get(1.0, sel_first))
            sel_end = len(self.text.get(1.0, sel_last))
            if sel_start == sel_end:
                sel_start = sel_end = None
        except tk.TclError:
            sel_start = sel_end = None
        chords = get_chords_to_operate(content, sel_start, sel_end, cursor_pos)
        has_two_note = any(len(c[2].rstrip("_").split("/")) == 2 for c in chords)

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="剪切", command=self._on_cut)
        menu.add_command(label="复制", command=self._on_copy_selection)
        menu.add_command(label="粘贴", command=self._on_paste)
        menu.add_separator()
        menu.add_command(label="全选", command=self._on_select_all)
        if chords:
            menu.add_separator()
            swap_state = tk.NORMAL if has_two_note else tk.DISABLED
            menu.add_command(label="和弦：交换两音", command=self._on_chord_swap, state=swap_state)
            menu.add_command(label="和弦：按音高升序", command=self._on_chord_sort_asc)
            menu.add_command(label="和弦：按音高降序", command=self._on_chord_sort_desc)
        menu.add_separator()
        dur_state = tk.NORMAL if (sel_start is not None and sel_end is not None) else tk.DISABLED
        menu.add_command(label="时值÷2", command=self._on_duration_divide_two, state=dur_state)
        menu.add_command(label="时值×2", command=self._on_duration_multiply_two, state=dur_state)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_copy_all(self):
        """复制全部内容到剪贴板"""
        content = self.text.get(1.0, tk.END)
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.root.update()
        self.status_label.config(text="已复制全部到剪贴板")
        self.root.after(2000, lambda: self.status_label.config(text="就绪"))

    def _get_selection_and_cursor(self) -> tuple[int | None, int | None, int | None]:
        """返回 (sel_start, sel_end, cursor_pos) 字符位置，0-based"""
        content = self.text.get(1.0, tk.END)
        try:
            sel_first = self.text.index(tk.SEL_FIRST)
            sel_last = self.text.index(tk.SEL_LAST)
            sel_start = len(self.text.get(1.0, sel_first))
            sel_end = len(self.text.get(1.0, sel_last))
            if sel_start == sel_end:
                sel_start = sel_end = None
        except tk.TclError:
            sel_start = sel_end = None
        try:
            insert_idx = self.text.index(tk.INSERT)
            cursor_pos = len(self.text.get(1.0, insert_idx))
        except tk.TclError:
            cursor_pos = None
        return sel_start, sel_end, cursor_pos

    def _apply_chord_operation(self, transform):
        """对选区/光标处的和弦应用变换。transform(chord_text, tonality_offset) -> new_text"""
        content = self.text.get(1.0, tk.END)
        sel_start, sel_end, cursor_pos = self._get_selection_and_cursor()
        chords = get_chords_to_operate(content, sel_start, sel_end, cursor_pos)
        if not chords:
            return
        tonality = get_tonality_offset(content)
        # 从后往前替换，避免位置偏移
        for start, end, chord_text in reversed(chords):
            new_text = transform(chord_text, tonality)
            if new_text is not None and new_text != chord_text:
                self.text.delete(f"1.0+{start}c", f"1.0+{end}c")
                self.text.insert(f"1.0+{start}c", new_text)
        self._schedule_auto_save()
        self._schedule_preview()
        self._do_highlights()

    def _on_chord_swap(self):
        """交换和弦中两音顺序（仅两音和弦）"""
        def transform(ct, _):
            return chord_swap_two(ct)
        self._apply_chord_operation(transform)

    def _on_chord_sort_asc(self):
        """按音高升序排列和弦内各音"""
        def transform(ct, to):
            return chord_sort(ct, ascending=True, tonality_offset=to)
        self._apply_chord_operation(transform)

    def _on_chord_sort_desc(self):
        """按音高降序排列和弦内各音"""
        def transform(ct, to):
            return chord_sort(ct, ascending=False, tonality_offset=to)
        self._apply_chord_operation(transform)

    def _apply_duration_operation(self, transform):
        """对选区内的音符应用时值变换。无选区则不操作。transform(token) -> new_token or None"""
        sel_start, sel_end, _ = self._get_selection_and_cursor()
        if sel_start is None or sel_end is None:
            return
        content = self.text.get(1.0, tk.END)
        tokens = find_note_tokens_in_range(content, sel_start, sel_end)
        if not tokens:
            return
        for start, end, tok in reversed(tokens):
            new_tok = transform(tok)
            if new_tok is not None and new_tok != tok:
                self.text.delete(f"1.0+{start}c", f"1.0+{end}c")
                self.text.insert(f"1.0+{start}c", new_tok)
        self._schedule_auto_save()
        self._schedule_preview()
        self._do_highlights()

    def _on_duration_divide_two(self):
        """时值除以2：每个音符后加 _（四分→八分→十六分，最多十六分）"""
        self._apply_duration_operation(duration_divide_two)

    def _on_duration_multiply_two(self):
        """时值乘以2：每个音符去掉一个 _（十六分→八分→四分，最多四分）"""
        self._apply_duration_operation(duration_multiply_two)

    def _on_voicevox_voices(self):
        """打开 VOICEVOX 音色选择对话框（音色列表 + 利用規約 + 试听 + 清唱生成）"""
        try:
            from voicevox_voice_dialog import show_voicevox_dialog
            get_score = lambda: self.text.get(1.0, tk.END)
            get_current_file = lambda: self.current_file_path
            show_voicevox_dialog(self.root, get_score_callback=get_score, get_current_file_callback=get_current_file)
        except ImportError as e:
            import traceback
            show_error_detail(self.root, "错误", f"无法加载 VOICEVOX 模块: {e}", traceback.format_exc())
    
    def _on_open_workspace(self):
        default_dir = WORKSPACES_DIR
        default_dir.mkdir(parents=True, exist_ok=True)
        path = filedialog.askdirectory(title="选择工作区文件夹", initialdir=str(default_dir))
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
            base = self._get_breakpoint_base_dir()
            self._breakpoints = set(load_breakpoints(base, path.name)) if base else set()
            self._do_highlights()
            self._highlight_current_file_in_workspace()
            self.root.after(100, self._update_preview)
        except Exception as e:
            import traceback
            show_error_detail(self.root, "打开失败", str(e), traceback.format_exc())
    
    def _save_to(self, path: Path, silent: bool = False):
        try:
            content = self.text.get(1.0, tk.END)
            path.write_text(content, encoding="utf-8")
            prev_path = self.current_file_path
            self.current_file_path = path
            self.root.title(f"简谱演奏 - {path.name}")
            if prev_path != path:
                base = self._get_breakpoint_base_dir()
                self._breakpoints = set(load_breakpoints(base, path.name)) if base else set()
            if not silent:
                self.status_label.config(text="已保存")
                self.root.after(2000, lambda: self.status_label.config(text="就绪"))
            if self.workspace_root and path.parent == self.workspace_root:
                self._refresh_workspace_list()
        except Exception as e:
            import traceback
            show_error_detail(self.root, "保存失败", str(e), traceback.format_exc())
    
    def _set_workspace(self, path: Path):
        self.workspace_root = path
        name = path.name if path else ""
        self._workspace_frame.config(text=f"工作区: {name}")
        self._refresh_workspace_list()
        _save_last_workspace(path)
    
    def _refresh_workspace_list(self):
        """刷新左侧工作区文件列表"""
        self._workspace_list.delete(0, tk.END)
        files = self._get_workspace_files()
        for f in files:
            self._workspace_list.insert(tk.END, f.name)
        self._highlight_current_file_in_workspace()

    def _highlight_current_file_in_workspace(self):
        """高亮工作区中当前打开的文件"""
        default_bg = "#2d2d2d" if self._dark_mode else "#ffffff"
        current_bg = getattr(self._workspace_list, "current_file_bg", "#d4f0d4")
        files = self._get_workspace_files()
        for i in range(self._workspace_list.size()):
            if i < len(files) and self.current_file_path and files[i].resolve() == self.current_file_path.resolve():
                self._workspace_list.itemconfig(i, bg=current_bg)
            else:
                self._workspace_list.itemconfig(i, bg=default_bg)
    
    def _on_workspace_drag_start(self, event=None):
        """记录拖拽源文件，供输入框 ButtonRelease 插入 \\import{文件名}"""
        idx = self._workspace_list.nearest(event.y)
        files = self._get_workspace_files()
        if 0 <= idx < len(files):
            self._drag_file = files[idx].name

    def _on_workspace_file_select(self, event=None):
        self._drag_file = None  # 双击打开文件，非拖拽，清除以免后续点文本框误插入
        sel = self._workspace_list.curselection()
        if not sel or not self.workspace_root:
            return
        idx = sel[0]
        files = self._get_workspace_files()
        if idx < len(files):
            self._load_file(files[idx])

    def _get_workspace_files(self) -> list:
        """获取工作区内的 .choir/.txt 文件列表（已排序）"""
        if not self.workspace_root or not self.workspace_root.is_dir():
            return []
        exts = {".choir", ".txt"}
        return sorted(
            f for f in self.workspace_root.iterdir()
            if f.is_file() and f.suffix.lower() in exts
        )

    def _apply_workspace_list_theme(self):
        """应用工作区列表的主题颜色"""
        if self._dark_mode:
            self._workspace_list.configure(
                bg="#2d2d2d",
                fg="#d4d4d4",
                selectbackground="#404040",
                selectforeground="#ffffff",
            )
            self._workspace_list.hover_bg = "#3d3d3d"
            self._workspace_list.current_file_bg = "#2a4a2a"
        else:
            self._workspace_list.configure(
                bg="#ffffff",
                fg="#000000",
                selectbackground="#cce8ff",
                selectforeground="#000000",
            )
            self._workspace_list.hover_bg = "#e8f4fc"
            self._workspace_list.current_file_bg = "#d4f0d4"

    def _on_workspace_motion(self, event):
        """悬浮高亮，当前打开文件保持高亮"""
        idx = self._workspace_list.nearest(event.y)
        if idx == self._workspace_hover_index:
            return
        self._workspace_hover_index = idx
        default_bg = "#2d2d2d" if self._dark_mode else "#ffffff"
        current_bg = getattr(self._workspace_list, "current_file_bg", "#d4f0d4")
        hover_bg = getattr(self._workspace_list, "hover_bg", "#e8f4fc")
        files = self._get_workspace_files()
        for i in range(self._workspace_list.size()):
            if i == idx:
                self._workspace_list.itemconfig(i, bg=hover_bg)
            elif i < len(files) and self.current_file_path and files[i].resolve() == self.current_file_path.resolve():
                self._workspace_list.itemconfig(i, bg=current_bg)
            else:
                self._workspace_list.itemconfig(i, bg=default_bg)

    def _on_workspace_leave(self, event):
        """离开列表时清除悬浮高亮，保留当前文件高亮"""
        self._workspace_hover_index = -1
        self._highlight_current_file_in_workspace()

    def _on_workspace_context_menu(self, event):
        """右键菜单：重命名、删除"""
        idx = self._workspace_list.nearest(event.y)
        if idx < 0:
            return
        files = self._get_workspace_files()
        if idx >= len(files):
            return
        self._workspace_list.selection_clear(0, tk.END)
        self._workspace_list.selection_set(idx)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="重命名", command=lambda: self._on_workspace_rename(idx))
        menu.add_command(label="删除", command=lambda: self._on_workspace_delete(idx))
        menu.tk_popup(event.x_root, event.y_root)

    def _on_workspace_rename(self, idx: int):
        """重命名工作区文件"""
        files = self._get_workspace_files()
        if idx >= len(files):
            return
        old_path = files[idx]
        new_name = _ask_rename("请输入新文件名：", old_path.name, self.root)
        if not new_name or not new_name.strip():
            return
        new_name = new_name.strip()
        if not new_name.endswith(".choir") and not new_name.endswith(".txt"):
            new_name += ".choir"
        new_path = old_path.parent / new_name
        if new_path == old_path:
            return
        if new_path.exists():
            show_error_detail(self.root, "重命名失败", f"文件已存在：{new_name}")
            return
        try:
            old_path.rename(new_path)
            base = self._get_breakpoint_base_dir()
            if base:
                rename_breakpoints(base, old_path.name, new_path.name)
            if self.current_file_path == old_path:
                self.current_file_path = new_path
                self._breakpoints = set(load_breakpoints(base, new_path.name)) if base else set()
                self.root.title(f"简谱演奏 - {new_path.name}")
            self._refresh_workspace_list()
            self.status_label.config(text="已重命名")
            self.root.after(2000, lambda: self.status_label.config(text="就绪"))
        except Exception as e:
            import traceback
            show_error_detail(self.root, "重命名失败", str(e), traceback.format_exc())

    def _on_workspace_delete(self, idx: int):
        """删除工作区文件"""
        files = self._get_workspace_files()
        if idx >= len(files):
            return
        path = files[idx]
        if not messagebox.askyesno("确认删除", f"确定要删除文件吗？\n{path.name}"):
            return
        try:
            path.unlink()
            if self.current_file_path == path:
                self.current_file_path = None
                self.text.delete(1.0, tk.END)
                self.root.title("简谱演奏 - 未命名")
            self._refresh_workspace_list()
            self.status_label.config(text="已删除")
            self.root.after(2000, lambda: self.status_label.config(text="就绪"))
        except Exception as e:
            import traceback
            show_error_detail(self.root, "删除失败", str(e), traceback.format_exc())
    
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
            font=_mono_font(12),
            selectmode=tk.SINGLE,
            activestyle="none",
            highlightthickness=0,
        )
        self._workspace_list.pack(fill=tk.BOTH, expand=True, pady=(0, 2))
        self._workspace_list.bind("<Double-Button-1>", self._on_workspace_file_select)
        self._workspace_list.bind("<ButtonPress-1>", self._on_workspace_drag_start)
        self._workspace_list.bind("<Motion>", self._on_workspace_motion)
        self._workspace_list.bind("<Leave>", self._on_workspace_leave)
        self._workspace_list.bind("<Button-3>", self._on_workspace_context_menu)
        self._workspace_list.bind("<Button-2>", self._on_workspace_context_menu)  # macOS 右键
        self._drag_file: str | None = None  # 拖拽到输入框时插入 \import{文件名}
        self._workspace_hover_index: int = -1
        self._apply_workspace_list_theme()
        ws_scroll = ttk.Scrollbar(self._workspace_frame, orient=tk.VERTICAL, command=self._workspace_list.yview)
        self._workspace_list.configure(yscrollcommand=ws_scroll.set)
        ws_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 工作区：优先使用上次打开的，否则示例工作区
        _ensure_example_workspace()
        last = _load_last_workspace()
        initial_workspace = last if last else EXAMPLE_WORKSPACE
        self._set_workspace(initial_workspace)
        # 默认选中并加载第一个文件
        files = self._get_workspace_files()
        if files:
            self._workspace_list.selection_set(0)
            first_file = files[0]
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
        self.btn_play_segment = ttk.Button(toolbar, text="▶ A-B 区间", command=self._on_play_segment)
        self.btn_play_segment.pack(side=tk.LEFT, padx=(0, 5))
        
        self.btn_stop = ttk.Button(toolbar, text="■ 停止", command=self._on_stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 5))
        
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        self.btn_duration_divide = ttk.Button(toolbar, text="时值÷2", command=self._on_duration_divide_two, state=tk.DISABLED)
        self.btn_duration_divide.pack(side=tk.LEFT, padx=(0, 2))
        self.btn_duration_multiply = ttk.Button(toolbar, text="时值×2", command=self._on_duration_multiply_two, state=tk.DISABLED)
        self.btn_duration_multiply.pack(side=tk.LEFT, padx=(0, 5))

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        self.btn_voicevox = ttk.Button(toolbar, text="VOICEVOX 音色", command=self._on_voicevox_voices)
        self.btn_voicevox.pack(side=tk.LEFT, padx=(0, 5))
        
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
        
        # 编辑区（左侧行号 + 正文）
        ttk.Label(main, text="简谱输入（支持 \\tonality、\\beat、\\bpm、\\no_bar_check）:").pack(anchor=tk.W)
        colors = self._theme_colors()
        editor_frame = ttk.Frame(main)
        editor_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        gutter_bg = "#e0e0e0" if not self._dark_mode else "#252525"
        line_num_bg = "#e8e8e8" if not self._dark_mode else "#2d2d2d"
        self._breakpoint_gutter = tk.Canvas(
            editor_frame,
            width=18,
            highlightthickness=0,
            bg=gutter_bg,
        )
        self._breakpoint_gutter.pack(side=tk.LEFT, fill=tk.Y)
        self._breakpoint_gutter.bind("<Button-1>", self._on_breakpoint_click)
        self._breakpoint_gutter.bind("<Motion>", self._on_breakpoint_motion)
        self._breakpoint_gutter.bind("<Leave>", self._on_breakpoint_leave)
        self._bp_tooltip: tk.Toplevel | None = None
        self._bp_tooltip_after_id: str | None = None
        self._text_tooltip: tk.Toplevel | None = None
        self._text_tooltip_after_id: str | None = None
        self._text_tooltip_voice_id: int = -1
        self._breakpoints: set[int] = set()  # 当前文件的断点行号
        self._line_numbers = tk.Canvas(
            editor_frame,
            width=42,
            highlightthickness=0,
            bg=line_num_bg,
        )
        self._line_numbers.pack(side=tk.LEFT, fill=tk.Y)
        # 等宽字体 + 不换行，便于 Ctrl+F 小节对齐
        self.text = scrolledtext.ScrolledText(
            editor_frame,
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
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hscroll.pack(fill=tk.X)
        # 行号与正文同步滚动
        _orig_yview = self.text.yview
        def _yview_wrapper(*args):
            _orig_yview(*args)
            self._redraw_line_numbers()
            self._redraw_breakpoints()
        self.text.vbar.configure(command=_yview_wrapper)
        def _schedule_redraw(*args):
            self.root.after(10, self._redraw_line_numbers)
            self.root.after(10, self._redraw_breakpoints)
        self.text.bind("<Button-1>", _schedule_redraw)
        self.text.bind("<Configure>", lambda e: self._redraw_line_numbers())
        self.text.bind("<MouseWheel>", _schedule_redraw)
        self.text.bind("<Button-4>", _schedule_redraw)  # Linux 滚轮上
        self.text.bind("<Button-5>", _schedule_redraw)  # Linux 滚轮下
        self.text.bind("<Motion>", self._on_text_motion)
        self.text.bind("<Leave>", self._on_text_leave)

        # 实时预览面板（带歌词简谱）
        self._preview_frame = ttk.LabelFrame(main, text="带歌词简谱预览", padding=5)
        self._preview_frame.pack(fill=tk.BOTH, expand=False, pady=(5, 0))
        self._preview_canvas = tk.Canvas(
            self._preview_frame,
            bg="#ffffff",
            highlightthickness=0,
            height=180,
        )
        prev_scroll_y = ttk.Scrollbar(self._preview_frame, orient=tk.VERTICAL, command=self._preview_canvas.yview)
        prev_scroll_x = ttk.Scrollbar(self._preview_frame, orient=tk.HORIZONTAL, command=self._preview_canvas.xview)
        self._preview_canvas.configure(yscrollcommand=prev_scroll_y.set, xscrollcommand=prev_scroll_x.set)
        self._preview_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        prev_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        prev_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self._preview_photo: tk.PhotoImage | None = None
        self._preview_timer = None

        # 默认加载工作区第一个文件（已由上方 _set_workspace 设置 current_file_path）
        if self.current_file_path and self.current_file_path.exists():
            self.text.insert(tk.END, self.current_file_path.read_text(encoding="utf-8"))
        else:
            fallback = EXAMPLE_WORKSPACE / "单声部.choir"
            if fallback.exists():
                self.text.insert(tk.END, fallback.read_text(encoding="utf-8"))
                self.current_file_path = fallback
            else:
                self.text.insert(tk.END, SAMPLE_SCORE)
        self.text.bind("<Control-f>", self._on_align)
        def _edit_bind(seq, handler):
            def _handler(e):
                handler()
                return "break"
            self.text.bind(seq, _handler)

        _edit_bind("<Control-x>", self._on_cut)
        _edit_bind("<Control-c>", self._on_copy_selection)
        _edit_bind("<Control-v>", self._on_paste)
        _edit_bind("<Control-a>", self._on_select_all)
        _edit_bind("<Control-Shift-C>", self._on_copy_all)
        if sys.platform == "darwin":
            _edit_bind("<Command-x>", self._on_cut)
            _edit_bind("<Command-c>", self._on_copy_selection)
            _edit_bind("<Command-v>", self._on_paste)
            _edit_bind("<Command-a>", self._on_select_all)
        self.root.after(100, self._do_highlights)
        self.root.after(150, self._update_preview)
        self.root.after(200, self._update_duration_buttons_state)
        self.text.bind("<KeyRelease>", self._on_key_release)
        self.text.bind("<ButtonRelease-1>", self._on_text_button_release)
        self.text.bind("<Button-3>", self._on_text_context_menu)
        self.text.bind("<Button-2>", self._on_text_context_menu)  # macOS 右键
        # 拖拽从 Listbox 到 Text 时，释放事件会发给 Listbox（鼠标被捕获），故用全局监听
        self.root.bind_all("<ButtonRelease-1>", self._on_global_drop_check)

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
            font=_mono_font(14),
            selectmode=tk.SINGLE,
        )
        self._diag_list.bind("<Button-1>", self._on_diag_select)
        diag_scroll = ttk.Scrollbar(self._diag_content_frame, orient=tk.VERTICAL, command=self._diag_list.yview)
        self._diag_list.configure(yscrollcommand=diag_scroll.set)
        self._diag_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        diag_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        for i, c in enumerate(colors["bracket"]):
            self.text.tag_configure(f"bracket{i}", background=c)
        # 用背景色表示错误/警告，避免与下划线字符 _ 重叠
        err_bg = "#ffcccc" if not self._dark_mode else "#5c2a2a"
        warn_bg = "#fff3cd" if not self._dark_mode else "#4a4020"
        self.text.tag_configure("diag_error", background=err_bg)
        self.text.tag_configure("diag_warning", background=warn_bg)
        self.text.tag_configure("comment", foreground="#2d5a2d" if not self._dark_mode else "#5a8a5a")
        self._bar_check_enabled = True  # \no_bar_check 时可关闭
        self._highlight_timer = None
        self._auto_save_timer = None
        
        self._update_diagnostics()

        # 底部说明（用 tk.Label 以支持主题色）
        hint_bg = "#2d2d2d" if self._dark_mode else "#f0f0f0"
        self.hint = tk.Label(
            main,
            text="支持: 1-7 音符, 0 休止, - 增加一拍, _ 缩短, . 八度, / 和弦, & 多声部, | 小节, ( )n n连音, ~ 连音线(可跨小节), # b ^ 升降还原, [xxx](...) 记号, [dc][fine] 反复, // 单行注释, \\tts{文本}{zh/ja/en}{voice_id} 篇章间语音(voice_id用VOICEVOX), \\lyrics{字/字}{part}{voice_id}{melody} 歌词(melody:0第一音1第二音), 1(啊) 行内歌词, \\import{文件名} 导入 | 文件→导出带歌词简谱(JPG) | Ctrl+F 对齐 | 括号高亮",
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
        
        def simple_split(s: str) -> tuple[str, list[str], str]:
            """拆分行：返回 (prefix, bars, suffix)。支持 [xxx](|bar1|bar2|) 记号内的小节"""
            parts = []
            cur = []
            i = 0
            n = len(s)
            in_notation_scope = False
            notation_paren_depth = 0
            depth = 0

            while i < n:
                c = s[i]
                # 检测 ]( 进入记号作用域
                if c == "]" and i + 1 < n and s[i + 1] == "(":
                    cur.append(c)
                    i += 1
                    in_notation_scope = True
                    notation_paren_depth = 1
                    cur.append("(")
                    i += 1
                    continue
                if in_notation_scope:
                    if c == "(":
                        notation_paren_depth += 1
                        cur.append(c)
                    elif c == ")":
                        notation_paren_depth -= 1
                        if notation_paren_depth == 0:
                            in_notation_scope = False
                        cur.append(c)
                    elif c == "|":
                        parts.append("".join(cur))
                        cur = []
                    else:
                        cur.append(c)
                    i += 1
                    continue
                # 非记号作用域：仅 depth==0 时拆分 |
                if c == "[":
                    depth += 1
                    cur.append(c)
                elif c == "]":
                    depth -= 1
                    cur.append(c)
                elif c == "(":
                    depth += 1
                    cur.append(c)
                elif c == ")":
                    depth -= 1
                    cur.append(c)
                elif c == "|" and depth == 0:
                    parts.append("".join(cur))
                    cur = []
                else:
                    cur.append(c)
                i += 1

            if cur:
                parts.append("".join(cur))
            prefix = parts[0].rstrip() if parts else ""
            suffix = ""
            if len(parts) >= 2 and "](|" in (parts[0] if parts else "") and parts[-1].strip() == ")":
                suffix = ")"
                bars = [p.strip() for p in parts[1:-1]]
            else:
                bars = [p.strip() for p in parts[1:] if p.strip()]
            return prefix, bars, suffix
        
        def align_section(part_lines: list[tuple[int, str]]) -> list[str]:
            all_data = []
            for _, line in part_lines:
                prefix, bars, suffix = simple_split(line)
                all_data.append((prefix, bars, suffix))
            max_bars = max(len(bars) for _, bars, _ in all_data)
            if max_bars == 0:
                return [L for _, L in part_lines]
            for item in all_data:
                prefix, bars, _ = item
                while len(bars) < max_bars:
                    bars.append("")
            col_widths = [max(max(len(bars[j]) for _, bars, _ in all_data), 1) for j in range(max_bars)]
            result = []
            for (_, line), (prefix, bars, suffix) in zip(part_lines, all_data):
                if not bars and max_bars > 0:
                    result.append(line)
                    continue
                padded = [b.ljust(col_widths[j]) for j, b in enumerate(bars)]
                sep = " |" if prefix.rstrip().endswith("&") else "|"
                result.append(prefix.rstrip() + sep + "|".join(padded) + suffix)
            return result
        
        new_sections: list[list[str]] = []
        for section in sections:
            part_lines = section
            if self.auto_wrap_var.get():
                any_long = any(len(L) > max_chars for _, L in part_lines)
                if any_long:
                    all_data = [simple_split(L) for _, L in part_lines]
                    n_bars = max(len(bars) for _, bars, _ in all_data)
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
                    for (prefix, bars, suffix) in all_data:
                        h = bars[:split_at] if len(bars) >= split_at else bars
                        t = bars[split_at:] if len(bars) > split_at else []
                        sep = " |" if prefix.rstrip().endswith("&") else "|"
                        if h:
                            head_section.append(prefix.rstrip() + sep + "|".join(h) + suffix)
                        if t:
                            tail_section.append(prefix.rstrip() + sep + "|".join(t) + suffix)
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
    
    def _redraw_line_numbers(self):
        """重绘左侧行号"""
        try:
            self._line_numbers.delete("all")
            line_num_fg = "#606366" if not self._dark_mode else "#808080"
            i = self.text.index("@0,0")
            while True:
                dline = self.text.dlineinfo(i)
                if dline is None:
                    break
                y = dline[1]
                linenum = str(i).split(".")[0]
                self._line_numbers.create_text(
                    38, y, anchor="ne", text=linenum, fill=line_num_fg, font=_mono_font(12)
                )
                i = self.text.index(f"{i}+1line")
        except (tk.TclError, AttributeError):
            pass

    def _get_breakpoint_base_dir(self) -> Path | None:
        """返回断点存储的目录（工作区根或文件所在目录）"""
        if self.workspace_root and self.workspace_root.is_dir():
            return self.workspace_root
        if self.current_file_path:
            return self.current_file_path.parent
        return None

    def _breakpoint_line_at_y(self, y: float) -> int | None:
        """返回 y 坐标对应的行号，若不在任一行则返回 None"""
        try:
            i = self.text.index("@0,0")
            while True:
                dline = self.text.dlineinfo(i)
                if dline is None:
                    return None
                _, dy, _, h = dline[:4]
                if dy <= y < dy + h:
                    return int(str(i).split(".")[0])
                i = self.text.index(f"{i}+1line")
        except (tk.TclError, ValueError):
            return None

    def _on_breakpoint_click(self, event) -> None:
        """断点 gutter 点击：切换该行断点，最多 2 个（A、B 区间）"""
        line_no = self._breakpoint_line_at_y(event.y)
        if line_no is None:
            return
        if line_no in self._breakpoints:
            self._breakpoints.discard(line_no)
        else:
            if len(self._breakpoints) >= 2:
                # 移除第一个（A），新行成为 A
                first = min(self._breakpoints)
                self._breakpoints.discard(first)
            self._breakpoints.add(line_no)
        self._save_breakpoints()
        self._redraw_breakpoints()

    def _on_breakpoint_motion(self, event) -> None:
        """断点 gutter 悬浮：状态栏 + 淡色悬浮提示（防抖 400ms）"""
        if self._bp_tooltip_after_id:
            self.root.after_cancel(self._bp_tooltip_after_id)
            self._bp_tooltip_after_id = None
        line_no = self._breakpoint_line_at_y(event.y)
        if line_no is None or line_no not in self._breakpoints:
            self._hide_bp_tooltip()
            self.status_label.config(text="点击行左侧设置 A、B 断点（最多 2 个）")
            return
        bp_list = sorted(self._breakpoints)
        idx = bp_list.index(line_no)
        label = "A" if idx == 0 else "B"
        text = f"{label} 断点（第 {line_no} 行）"
        self.status_label.config(text=text)
        ev_x, ev_y = event.x, event.y

        def _show():
            self._bp_tooltip_after_id = None
            self._show_bp_tooltip(ev_x, ev_y, text)

        self._bp_tooltip_after_id = self.root.after(400, _show)

    def _show_bp_tooltip(self, x: int, y: int, text: str) -> None:
        """显示淡色悬浮断点提示"""
        if self._bp_tooltip is None:
            self._bp_tooltip = tk.Toplevel(self.root)
            self._bp_tooltip.withdraw()
            self._bp_tooltip.overrideredirect(True)
            self._bp_tooltip.attributes("-topmost", True)
            self._bp_tooltip_lbl = tk.Label(
                self._bp_tooltip,
                text="",
                bg="#f0f4e8" if not self._dark_mode else "#3a4035",
                fg="#333" if not self._dark_mode else "#ccc",
                font=("", 10),
                padx=8,
                pady=4,
                relief=tk.SOLID,
                borderwidth=1,
            )
            self._bp_tooltip_lbl.pack()
        self._bp_tooltip_lbl.config(text=text)
        self._bp_tooltip.deiconify()
        try:
            rx = self._breakpoint_gutter.winfo_rootx() + x + 14
            ry = self._breakpoint_gutter.winfo_rooty() + y + 4
            self._bp_tooltip.geometry(f"+{rx}+{ry}")
        except tk.TclError:
            pass

    def _hide_bp_tooltip(self) -> None:
        """隐藏断点悬浮提示"""
        if self._bp_tooltip_after_id:
            try:
                self.root.after_cancel(self._bp_tooltip_after_id)
            except tk.TclError:
                pass
            self._bp_tooltip_after_id = None
        if self._bp_tooltip is not None:
            try:
                self._bp_tooltip.withdraw()
            except tk.TclError:
                pass

    def _on_breakpoint_leave(self, event) -> None:
        """离开断点 gutter 时恢复状态栏并隐藏悬浮提示"""
        self._hide_bp_tooltip()
        if not self.is_playing:
            self.status_label.config(text="就绪")
        self._update_status_bar()

    def _parse_hover_token(self, content: str, char_pos: int) -> tuple[str, int] | None:
        """解析 char_pos 处的 token，返回 (提示文本, voice_id 或 -1)。"""
        lines = content.split("\n")
        offset = 0
        for line in lines:
            line_len = len(line) + 1
            if offset <= char_pos < offset + len(line):
                col = char_pos - offset
                # \bpm{120} \tonality{0} \beat{4/4} \no_bar_check
                for m in re.finditer(r"\\(bpm|tonality|beat|no_bar_check)\s*(\{[^}]*\})?", line, re.I):
                    if m.start() <= col <= m.end():
                        key = m.group(1).lower()
                        if key == "bpm" and m.group(2):
                            val = m.group(2)[1:-1]
                            return (f"BPM: {val}", -1)
                        if key == "tonality" and m.group(2):
                            val = m.group(2)[1:-1]
                            return (f"调性: {val}", -1)
                        if key == "beat" and m.group(2):
                            val = m.group(2)[1:-1]
                            return (f"拍号: {val}", -1)
                        if key == "no_bar_check":
                            return ("禁用小节时值检查", -1)
                        return (f"\\{key}", -1)
                # \lyrics{...}{part}{voice_id}{melody} 或 \tts{...}{lang}{voice_id}
                for m in re.finditer(r"\\lyrics\s*\{[^{}]*\}\s*\{[^{}]*\}\s*\{([^{}]+)\}", line, re.I):
                    if m.start(1) <= col <= m.end(1):
                        vid = m.group(1).strip()
                        if vid.isdigit():
                            return (f"歌词音色 ID: {vid}", int(vid))
                for m in re.finditer(r"\\tts\s*\{[^{}]*\}\s*\{[^{}]*\}\s*\{([^{}]+)\}", line, re.I):
                    if m.start(1) <= col <= m.end(1):
                        vid = m.group(1).strip()
                        if vid.isdigit():
                            return (f"TTS 音色 ID: {vid}", int(vid))
                break
            offset += line_len
        return None

    def _on_text_motion(self, event) -> None:
        """正文悬浮：\\bpm、\\tonality、\\beat、voice_id 等提示（防抖 500ms）"""
        if self._text_tooltip_after_id:
            self.root.after_cancel(self._text_tooltip_after_id)
            self._text_tooltip_after_id = None
        try:
            idx = self.text.index(f"@{event.x},{event.y}")
        except tk.TclError:
            self._hide_text_tooltip()
            return
        content = self.text.get(1.0, tk.END)
        char_pos = len(self.text.get(1.0, idx))
        parsed = self._parse_hover_token(content, char_pos)
        if parsed is None:
            self._hide_text_tooltip()
            return
        text, voice_id = parsed

        def _show():
            self._text_tooltip_after_id = None
            self._show_text_tooltip(event, text, voice_id)

        self._text_tooltip_after_id = self.root.after(500, _show)

    def _show_text_tooltip(self, event, text: str, voice_id: int) -> None:
        """显示正文悬浮提示，voice_id>=0 时异步加载头像"""
        self._text_tooltip_voice_id = voice_id
        if self._text_tooltip is None:
            self._text_tooltip = tk.Toplevel(self.root)
            self._text_tooltip.withdraw()
            self._text_tooltip.overrideredirect(True)
            self._text_tooltip.attributes("-topmost", True)
            self._text_tooltip_frame = tk.Frame(
                self._text_tooltip,
                bg="#f0f4e8" if not self._dark_mode else "#3a4035",
                relief=tk.SOLID,
                borderwidth=1,
            )
            self._text_tooltip_frame.pack()
            self._text_tooltip_lbl = tk.Label(
                self._text_tooltip_frame,
                text="",
                bg="#f0f4e8" if not self._dark_mode else "#3a4035",
                fg="#333" if not self._dark_mode else "#ccc",
                font=("", 10),
                padx=8,
                pady=4,
            )
            self._text_tooltip_lbl.pack()
            self._text_tooltip_img = tk.Label(self._text_tooltip_frame, image="", bg="#f0f4e8" if not self._dark_mode else "#3a4035")
        self._text_tooltip_img.pack_forget()
        self._text_tooltip_lbl.config(text=text)
        self._text_tooltip_lbl.pack()
        try:
            rx = self.text.winfo_rootx() + event.x + 16
            ry = self.text.winfo_rooty() + event.y + 20
            self._text_tooltip.deiconify()
            self._text_tooltip.geometry(f"+{rx}+{ry}")
        except tk.TclError:
            return
        if voice_id >= 0:
            self._load_voice_avatar_async(voice_id, voice_id)

    def _load_voice_avatar_async(self, style_id: int, request_id: int) -> None:
        """后台加载 VOICEVOX 音色头像并更新 tooltip"""
        cache = getattr(self, "_voice_avatar_cache", None)
        if cache is None:
            self._voice_avatar_cache = {}
            cache = self._voice_avatar_cache
        if style_id in cache:
            self._update_tooltip_avatar(cache[style_id], style_id)
            return

        def _fetch():
            try:
                from voicevox_client import fetch_speakers, fetch_singers, fetch_speaker_info, resolve_speakers_style_id
                base = VOICEVOX_BASE
                singers = fetch_singers(base)
                uuid_val = None
                for s in singers:
                    for st in s.get("styles", []):
                        if st.get("id") == style_id:
                            uuid_val = s.get("speaker_uuid") or s.get("uuid")
                            break
                    if uuid_val:
                        break
                if not uuid_val:
                    speakers = fetch_speakers(base)
                    for sp in speakers:
                        for st in sp.get("styles", []):
                            if st.get("id") == style_id:
                                uuid_val = sp.get("speaker_uuid") or sp.get("uuid")
                                break
                        if uuid_val:
                            break
                if not uuid_val:
                    return
                from voicevox_client import resolve_speakers_style_id
                match_id = resolve_speakers_style_id(style_id, base) or style_id
                for fmt in ("url", "base64"):
                    try:
                        info = fetch_speaker_info(str(uuid_val), base, resource_format=fmt)
                        portrait = None
                        for si in info.get("style_infos") or []:
                            if si.get("id") in (style_id, match_id) and si.get("portrait"):
                                portrait = si["portrait"]
                                break
                        if not portrait:
                            portrait = info.get("portrait") or ""
                        if not portrait and info.get("style_infos"):
                            portrait = info["style_infos"][0].get("portrait", "")
                        if portrait:
                            is_url = fmt == "url" and (portrait.startswith("http://") or portrait.startswith("https://"))
                            from voicevox_voice_dialog import _load_portrait_image
                            img = _load_portrait_image(portrait, is_url, (64, 64))
                            if img:
                                rid = request_id
                                self.root.after(0, lambda i=img, r=rid: self._update_tooltip_avatar(i, r))
                                self._voice_avatar_cache[style_id] = img
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _update_tooltip_avatar(self, photo: tk.PhotoImage, request_id: int) -> None:
        """更新正文 tooltip 显示头像，仅当 request_id 与当前 tooltip 的 voice_id 一致时更新"""
        if self._text_tooltip is None or not self._text_tooltip.winfo_viewable():
            return
        if getattr(self, "_text_tooltip_voice_id", -1) != request_id:
            return
        try:
            self._text_tooltip_img.config(image=photo)
            self._text_tooltip_img.image = photo
            self._text_tooltip_img.pack(pady=(0, 4))
        except tk.TclError:
            pass

    def _hide_text_tooltip(self) -> None:
        """隐藏正文悬浮提示"""
        self._text_tooltip_voice_id = -1
        if self._text_tooltip_after_id:
            try:
                self.root.after_cancel(self._text_tooltip_after_id)
            except tk.TclError:
                pass
            self._text_tooltip_after_id = None
        if self._text_tooltip is not None:
            try:
                self._text_tooltip.withdraw()
            except tk.TclError:
                pass

    def _on_text_leave(self, event) -> None:
        """离开正文时隐藏悬浮提示"""
        self._hide_text_tooltip()

    def _save_breakpoints(self) -> None:
        """将当前断点写入隐藏文件"""
        base = self._get_breakpoint_base_dir()
        if not base or not self.current_file_path:
            return
        save_breakpoints(base, self.current_file_path.name, list(self._breakpoints))

    def _redraw_breakpoints(self) -> None:
        """重绘断点 gutter，A 用红、B 用蓝区分"""
        try:
            self._breakpoint_gutter.delete("all")
            bp_list = sorted(self._breakpoints)
            color_a = "#c55" if not self._dark_mode else "#e66"
            color_b = "#55c" if not self._dark_mode else "#66e"
            i = self.text.index("@0,0")
            while True:
                dline = self.text.dlineinfo(i)
                if dline is None:
                    break
                y = dline[1]
                line_no = int(str(i).split(".")[0])
                if line_no in self._breakpoints:
                    idx = bp_list.index(line_no)
                    color = color_a if idx == 0 else color_b
                    cx, cy = 9, y + 8
                    self._breakpoint_gutter.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=color, outline=color)
                i = self.text.index(f"{i}+1line")
        except (tk.TclError, AttributeError):
            pass

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

    def _on_global_drop_check(self, event=None):
        """全局 ButtonRelease：从工作区拖到输入框时，释放事件会发给 Listbox，需在此处理"""
        if not getattr(self, "_drag_file", None):
            return
        try:
            rx, ry = self.root.winfo_pointerxy()
            w = self.root.winfo_containing(rx, ry)
            over_text = w == self.text or self._is_descendant(w, self.text)
            if not over_text:
                self._drag_file = None  # 释放在非输入框处，清除，避免双击打开后点文本框误插入
                return
            tx, ty = self.text.winfo_rootx(), self.text.winfo_rooty()
            rel_x, rel_y = rx - tx, ry - ty
            idx = self.text.index(f"@{rel_x},{rel_y}")
            self.text.insert(idx, f"\\import{{{self._drag_file}}}")
            self._drag_file = None
            self._schedule_auto_save()
            self.root.after(50, self._do_highlights)
            self.root.after(50, self._update_status_bar)
            self._schedule_preview()
        except (tk.TclError, AttributeError):
            pass

    def _is_descendant(self, w: tk.Widget, ancestor: tk.Widget) -> bool:
        """判断 w 是否为 ancestor 的后代"""
        while w:
            if w == ancestor:
                return True
            try:
                w = w.master
            except AttributeError:
                break
        return False

    def _on_text_button_release(self, event=None):
        """处理输入框内点击/释放，更新状态栏；拖放由 _on_global_drop_check 处理"""
        self.text.tag_raise("sel")  # 选区显示在高亮之上
        self._on_cursor_move(event)

    def _update_duration_buttons_state(self):
        """无选区时灰掉时值按钮"""
        sel_start, sel_end, _ = self._get_selection_and_cursor()
        has_selection = sel_start is not None and sel_end is not None
        state = tk.NORMAL if has_selection else tk.DISABLED
        self.btn_duration_divide.config(state=state)
        self.btn_duration_multiply.config(state=state)

    def _on_cursor_move(self, event=None):
        self._update_status_bar()
        self._update_duration_buttons_state()

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
        voicevox_unreachable = any(d.message == VOICEVOX_UNREACHABLE_MSG for d in diags)
        if hasattr(self, "btn_voicevox"):
            self.btn_voicevox.config(state=tk.DISABLED if voicevox_unreachable else tk.NORMAL)
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
        """点击诊断项时跳转到对应位置"""
        if not getattr(self, "_diag_data", None):
            return
        if event is not None and event.widget == self._diag_list:
            idx = self._diag_list.nearest(event.y)
        else:
            sel = self._diag_list.curselection()
            if not sel:
                return
            idx = sel[0]
        if idx < len(self._diag_data):
            d = self._diag_data[idx]
            if d.start_pos is not None:
                try:
                    target = self.text.index(f"1.0+{d.start_pos}c")
                    self.text.see(target)
                    self.text.mark_set(tk.INSERT, target)
                except tk.TclError:
                    self.text.see(f"{d.line}.{d.column}")
                    self.text.mark_set(tk.INSERT, f"{d.line}.{d.column}")
            else:
                self.text.see(f"{d.line}.{d.column}")
                self.text.mark_set(tk.INSERT, f"{d.line}.{d.column}")
            self.text.focus_set()

    def _highlight_comments(self):
        """// 单行注释显示为深绿色"""
        self.text.tag_remove("comment", "1.0", tk.END)
        content = self.text.get(1.0, tk.END)
        for m in re.finditer(r"//[^\n]*", content):
            start, end = m.span()
            self.text.tag_add("comment", f"1.0+{start}c", f"1.0+{end}c")

    def _do_highlights(self):
        """执行括号高亮、注释高亮、诊断背景色（错误红/警告黄）、行号、断点"""
        self._highlight_brackets()
        self._highlight_comments()
        self._update_diagnostics()
        self._redraw_line_numbers()
        self._redraw_breakpoints()
        self.text.tag_raise("sel")  # 选区显示在高亮之上
    
    def _on_key_release(self, event=None):
        if self._highlight_timer:
            self.text.after_cancel(self._highlight_timer)
        self._highlight_timer = self.text.after(300, self._do_highlights)
        self.root.after(50, self._update_status_bar)
        self._update_duration_buttons_state()
        self._schedule_auto_save()
        self._schedule_preview()
    
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

    def _schedule_preview(self):
        """延迟更新预览，避免输入时频繁渲染"""
        if self._preview_timer:
            self.root.after_cancel(self._preview_timer)
        self._preview_timer = self.root.after(400, self._update_preview)

    def _update_preview(self):
        """实时渲染带歌词简谱预览"""
        self._preview_timer = None
        try:
            from PIL import ImageTk
        except ImportError:
            return
        content = self.text.get(1.0, tk.END)
        if not content.strip():
            self._preview_canvas.delete("all")
            return
        base_dir = (
            self.workspace_root
            if self.workspace_root and self.workspace_root.is_dir()
            else (self.current_file_path.parent if self.current_file_path else Path.cwd())
        )
        try:
            content = expand_imports(content, base_dir)
        except Exception:
            pass
        try:
            score = parse(content)
        except Exception:
            return
        try:
            pil_img = render_to_pil(score, layout="vertical", font_size=18)
        except Exception:
            return
        w, h = pil_img.size
        photo = ImageTk.PhotoImage(pil_img)
        self._preview_photo = photo
        self._preview_canvas.delete("all")
        self._preview_canvas.config(scrollregion=(0, 0, w, h))
        self._preview_canvas.create_image(0, 0, anchor=tk.NW, image=photo)
    
    def _on_play_segment(self):
        """从 A 断点播放到 B 断点"""
        if self.is_playing:
            return
        bp_list = sorted(self._breakpoints)
        if len(bp_list) < 2:
            messagebox.showinfo("A-B 区间", "请设置 A、B 两个断点（点击行左侧，最多 2 个）")
            return
        start_line, end_line = bp_list[0], bp_list[1]
        content = self.text.get(1.0, tk.END)
        lines = content.split("\n")
        # 收集 A 断点之前的 tonality、beat、bpm、no_bar_check 等全局设置
        setting_pat = re.compile(r"\\tonality\{|\\beat\{|\\bpm\{|\\no_bar_check", re.I)
        header_lines = [
            ln for ln in lines[: start_line - 1]
            if setting_pat.search(ln)
        ]
        segment_lines = lines[start_line - 1 : end_line - 1]
        excerpt = "\n".join(header_lines + [""] + segment_lines) if header_lines else "\n".join(segment_lines)
        if not excerpt.strip():
            messagebox.showinfo("断点区间", "区间内无内容")
            return
        base_dir = (
            self.workspace_root
            if self.workspace_root and self.workspace_root.is_dir()
            else (self.current_file_path.parent if self.current_file_path else Path.cwd())
        )
        try:
            excerpt = expand_imports(excerpt, base_dir)
        except (FileNotFoundError, ValueError, OSError) as e:
            import traceback
            show_error_detail(self.root, "导入错误", str(e), traceback.format_exc())
            return
        self.is_playing = True
        self.btn_play.config(state=tk.DISABLED)
        self.btn_play_segment.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.status_label.config(text="播放 A-B 区间...")

        def progress_cb(current: float, total: float):
            if total > 0:
                pct = current / total * 100
                self.root.after(0, lambda: self.progress_var.set(pct))

        self.player.set_progress_callback(progress_cb)

        def run():
            try:
                self.player.play_score(excerpt)
            except Exception as e:
                import traceback
                err_msg, tb = str(e), traceback.format_exc()
                self.root.after(0, lambda: show_error_detail(self.root, "播放错误", err_msg, tb))
            finally:
                self.root.after(0, self._on_play_finished)

        self.play_thread = threading.Thread(target=run, daemon=True)
        self.play_thread.start()

    def _on_play(self):
        if self.is_playing:
            return
        score = self.text.get(1.0, tk.END)
        if not score.strip():
            messagebox.showwarning("提示", "请输入简谱内容")
            return
        base_dir = (
            self.workspace_root
            if self.workspace_root and self.workspace_root.is_dir()
            else (self.current_file_path.parent if self.current_file_path else Path.cwd())
        )
        try:
            score = expand_imports(score, base_dir)
        except (FileNotFoundError, ValueError, OSError) as e:
            import traceback
            show_error_detail(self.root, "导入错误", str(e), traceback.format_exc())
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
                import traceback
                err_msg, tb = str(e), traceback.format_exc()
                self.root.after(0, lambda: show_error_detail(self.root, "播放错误", err_msg, tb))
            finally:
                self.root.after(0, self._on_play_finished)
        
        self.play_thread = threading.Thread(target=run, daemon=True)
        self.play_thread.start()
    
    def _on_play_finished(self):
        self.is_playing = False
        self.btn_play.config(state=tk.NORMAL)
        self.btn_play_segment.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.progress_var.set(100)
        self.status_label.config(text="播放完成")
    
    def _on_stop(self):
        self.player.stop()
        self.status_label.config(text="已停止")
    
    def run(self):
        def _grab_focus():
            self.root.lift()
            self.root.focus_force()
        self.root.after(100, _grab_focus)
        self.root.mainloop()


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
