"""
输入补全：输入 \\ 或 [ 时弹出 VS Code 风格的补全列表。
来源：乐器、语法命令、当前 scope、\\define 及 lib 中的 define。
"""
import re
import tkinter as tk
from pathlib import Path
from typing import Callable

from src.core.preprocessor import expand_imports, LIB_DIR

# 反斜杠命令补全：(名称, 插入内容, 光标偏移) 光标偏移为插入后从末尾回退字符数，便于编辑括号内
BACKSLASH_COMMANDS = [
    ("tonality", "\\tonality{0}", 1),
    ("beat", "\\beat{4/4}", 1),
    ("bpm", "\\bpm{120}", 1),
    ("no_bar_check", "\\no_bar_check", None),
    ("import", "\\import{文件名}", 1),
    ("define", "\\define{名称}{内容}", 1),
    ("lyrics", "\\lyrics{字/字}{0}{0}{0}", 1),
    ("tts", "\\tts{文本}{zh}{0}", 1),
]

# 方括号补全：乐器 + 记号。需作用域的记号补全时带 () 及光标偏移
BRACKET_INSTRUMENTS = [
    "grand_piano", "piano", "violin", "cello", "trumpet", "clarinet",
    "oboe", "alto_sax", "tenor_sax", "bass", "guitar", "drums", "guitar_electrical", "bass_electrical"
]
BRACKET_NOTATIONS = [
    "8vb", "8va", "15va", "15vb",
    "ff", "f", "mf", "mp", "p", "pp", "ppp",
    "dc", "fine", "a", "arpeggio", "gliss",
    "+3", "-3", "+5", "-5",
]
BRACKET_NOTATIONS_WITH_SCOPE = {"8vb", "8va", "15va", "15vb", "a", "arpeggio", "gliss"}


def _extract_defines_from_text(text: str) -> set[str]:
    """从文本中提取 \\define{key}{value} 的 key"""
    result: set[str] = set()
    for m in re.finditer(r"\\define\{([^{}]+)\}\{", text, re.I):
        key = m.group(1).strip()
        if key:
            result.add(key)
    return result


def _get_defines_from_content(content: str, base_dir: Path | None) -> set[str]:
    """从当前内容及 \\import 展开后的内容中提取 define 名称"""
    defines = _extract_defines_from_text(content)
    if not base_dir or not base_dir.is_dir():
        return defines
    try:
        expanded = expand_imports(content, base_dir)
        defines.update(_extract_defines_from_text(expanded))
    except Exception:
        pass
    # 从 lib 目录扫描 define 文件（限制数量避免过多）
    if LIB_DIR.is_dir():
        for f in sorted(LIB_DIR.glob("*.choir"))[:5]:
            try:
                lib_text = f.read_text(encoding="utf-8")
                defines.update(_extract_defines_from_text(lib_text))
                if len(defines) > 200:
                    break
            except Exception:
                pass
    return defines


def get_backslash_suggestions(prefix: str) -> list[tuple[str, str, int | None]]:
    r"""返回 \ 命令补全，prefix 为 \ 之后的已输入部分。返回 (名称, 插入内容, 光标偏移)"""
    prefix = prefix.lower().strip()
    result = []
    for item in BACKSLASH_COMMANDS:
        name, insert = item[0], item[1]
        cursor_offset = item[2] if len(item) > 2 else None
        if name.lower().startswith(prefix):
            result.append((name, insert, cursor_offset))
    return result


def get_bracket_suggestions(
    prefix: str,
    content: str,
    base_dir: Path | None,
) -> list[tuple[str, str, int | None]]:
    """返回 [ 补全：乐器、记号、define 名称。需作用域的记号带 () 及光标偏移"""
    prefix_lower = prefix.lower().strip()
    result: list[tuple[str, str, int | None]] = []
    for name in BRACKET_INSTRUMENTS:
        if name.lower().startswith(prefix_lower):
            result.append((name, f"[{name}]", None))
    for name in BRACKET_NOTATIONS:
        if name.lower().startswith(prefix_lower):
            if name in BRACKET_NOTATIONS_WITH_SCOPE:
                result.append((name, f"[{name}]()", 1))
            else:
                result.append((name, f"[{name}]", None))
    defines = _get_defines_from_content(content, base_dir)
    for name in sorted(defines):
        if name.lower().startswith(prefix_lower) and not any(r[0] == name for r in result):
            result.append((name, f"[{name}]", None))
    return result


def show_autocomplete_popup(
    parent: tk.Widget,
    text_widget: tk.Text,
    suggestions: list[tuple[str, str]],
    trigger_pos: int,
    insert_text: str,
    on_select: Callable[[str], None],
) -> "AutocompletePopup | None":
    """
    显示补全弹窗。suggestions: [(display, insert_text), ...]
    trigger_pos: 触发位置（字符偏移），用于替换范围
    insert_text: 已输入的前缀，选中项将替换/补全此后缀
    """
    if not suggestions:
        return None
    return AutocompletePopup(
        parent, text_widget, suggestions, trigger_pos, insert_text, on_select
    )


class AutocompletePopup:
    """补全弹窗，支持上下键选择、回车插入"""

    def __init__(
        self,
        parent: tk.Widget,
        text_widget: tk.Text,
        suggestions: list[tuple[str, str]],
        trigger_pos: int,
        prefix: str,
        on_select: Callable[[str], None],
    ):
        self.text_widget = text_widget
        self.suggestions = suggestions
        self.trigger_pos = trigger_pos
        self.prefix = prefix
        self.on_select = on_select
        self.selected_index = 0

        self.popup = tk.Toplevel(parent)
        self.popup.wm_overrideredirect(True)
        self.popup.wm_geometry("+0+0")
        self.listbox = tk.Listbox(
            self.popup,
            height=min(10, len(suggestions)),
            font=("", 11),
            selectmode=tk.SINGLE,
            activestyle="none",
            highlightthickness=0,
        )
        scroll = tk.Scrollbar(self.popup, command=self.listbox.yview)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.configure(yscrollcommand=scroll.set)

        for item in suggestions:
            self.listbox.insert(tk.END, item[0])
        self.listbox.selection_set(0)
        self.listbox.see(0)

        self._place_near_cursor()
        # 不抢焦点，保持文本框可继续输入以过滤

        self.listbox.bind("<Return>", self._on_enter)
        self.listbox.bind("<Escape>", self._on_escape)
        self.listbox.bind("<Up>", self._on_up)
        self.listbox.bind("<Down>", self._on_down)
        self.listbox.bind("<Button-1>", self._on_click)

    def _place_near_cursor(self):
        self.text_widget.update_idletasks()
        idx = self.text_widget.index("insert")
        self.text_widget.see(idx)
        bbox = self.text_widget.bbox(idx)
        if not bbox:
            return
        x, y, w, h = bbox
        # 紧贴光标：对齐光标左缘，垂直紧接下行（1px 间距）
        root_x = self.text_widget.winfo_rootx() + x
        root_y = self.text_widget.winfo_rooty() + y + h + 1
        self.popup.wm_geometry(f"+{root_x}+{root_y}")

    def _on_enter(self, event=None):
        if 0 <= self.selected_index < len(self.suggestions):
            item = self.suggestions[self.selected_index]
            insert_val = item[1]
            cursor_offset = item[2] if len(item) > 2 else None
            self.close()
            self.on_select(insert_val, cursor_offset)
        return "break"

    def _on_escape(self, event=None):
        self.close()
        return "break"

    def _on_up(self, event=None):
        self.selected_index = max(0, self.selected_index - 1)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(self.selected_index)
        self.listbox.see(self.selected_index)
        return "break"

    def _on_down(self, event=None):
        self.selected_index = min(len(self.suggestions) - 1, self.selected_index + 1)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(self.selected_index)
        self.listbox.see(self.selected_index)
        return "break"

    def _on_click(self, event=None):
        sel = self.listbox.curselection()
        if sel:
            self.selected_index = sel[0]
            self._on_enter()
        return "break"

    def _on_focus_out(self, event=None):
        if event and event.widget == self.popup:
            return
        self.close()

    def update_suggestions(self, suggestions: list[tuple[str, str, int | None]]):
        """更新候选列表（用户继续输入时过滤）"""
        self.suggestions = suggestions
        self.listbox.delete(0, tk.END)
        for item in suggestions:
            self.listbox.insert(tk.END, item[0])
        self.listbox.configure(height=min(10, len(suggestions)))
        self.selected_index = 0
        if suggestions:
            self.listbox.selection_set(0)
            self.listbox.see(0)

    def close(self):
        try:
            self.popup.destroy()
        except tk.TclError:
            pass
