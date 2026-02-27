"""
输入补全：输入 \\ 或 [ 时弹出 VS Code 风格的补全列表。
来源：乐器、语法命令、当前 scope、\\define 及 lib 中的 define。
和弦补全：输入字母且不在方括号中时，弹出和弦补全（灰色小字展示将插入部分）。
"""
import re
import tkinter as tk
from pathlib import Path
from typing import Callable

from src.core.preprocessor import expand_imports, LIB_DIR
from src.utils.chord_completion import get_chord_suggestions

# 反斜杠命令补全：(名称, 插入内容, 光标偏移) 光标偏移为插入后从末尾回退字符数，便于编辑括号内
BACKSLASH_COMMANDS = [
    ("tonality", "\\tonality{0}", 1),
    ("beat", "\\beat{4/4}", 1),
    ("bpm", "\\bpm{120}", 1),
    ("reverb", "\\reverb{30}", 1),
    ("no_bar_check", "\\no_bar_check", None),
    ("import", "\\import{文件名}", 1),
    ("define", "\\define{名称}{内容}", 1),
    ("lyrics", "\\lyrics{字/字}{0}{0}{0}", 1),
    ("tts", "\\tts{文本}{zh}{0}", 1),
]

# 方括号补全：乐器 + 记号。需作用域的记号补全时带 () 及光标偏移
BRACKET_INSTRUMENTS = [
    "grand_piano", "piano", "violin", "cello", "trumpet", "clarinet",
    "oboe", "alto_sax", "tenor_sax", "bass", "guitar", "drums", "guitar_electric", "bass_electric"
]
BRACKET_NOTATIONS = [
    "8vb", "8va", "15va", "15vb",
    "ff", "f", "mf", "mp", "p", "pp", "ppp",
    "dc", "fine", "a", "arpeggio", "gliss", "r", "tr",
    "+3", "-3", "+5", "-5",
    "distortion:50", "drive:80",  # 电吉他失真 0-100
]
BRACKET_NOTATIONS_WITH_SCOPE = {"8vb", "8va", "15va", "15vb", "a", "arpeggio", "gliss", "tr"}


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
    result, _ = _get_defines_with_sources(content, base_dir, None)
    return result


def _get_defines_with_sources(
    content: str,
    base_dir: Path | None,
    current_filename: str | None,
) -> tuple[set[str], dict[str, str]]:
    """
    提取 define 名称及来源文件。
    返回 (defines_set, name_to_source)。
    source 示例："当前文件"、"lib/chords.choir"、"导入"
    """
    defines: set[str] = set()
    name_to_source: dict[str, str] = {}

    def _add(name: str, source: str) -> None:
        if name and name not in name_to_source:
            name_to_source[name] = source
        defines.add(name)

    # 当前内容（优先）
    for name in _extract_defines_from_text(content):
        _add(name, current_filename or "当前文件")

    # 展开导入后的内容（新增的 define 来自导入）
    if base_dir and base_dir.is_dir():
        try:
            expanded = expand_imports(content, base_dir)
            for name in _extract_defines_from_text(expanded):
                _add(name, "导入")
        except Exception:
            pass

    # lib 目录：可精确到文件
    if LIB_DIR.is_dir():
        for f in sorted(LIB_DIR.glob("*.choir"))[:5]:
            try:
                lib_text = f.read_text(encoding="utf-8")
                for name in _extract_defines_from_text(lib_text):
                    _add(name, f"lib/{f.name}")
                if len(defines) > 200:
                    break
            except Exception:
                pass
    return defines, name_to_source


# 统一补全格式：(display, insert, notes_str, cursor_offset)
# notes_str: 灰色小字展示（如简谱音），可为 None
# cursor_offset: 插入后光标从末尾回退字符数，可为 None


def get_backslash_suggestions(prefix: str) -> list[tuple[str, str, str | None, int | None]]:
    r"""返回 \ 命令补全。格式 (display, insert, notes_str, cursor_offset)"""
    prefix = prefix.lower().strip()
    result = []
    for item in BACKSLASH_COMMANDS:
        name, insert = item[0], item[1]
        cursor_offset = item[2] if len(item) > 2 else None
        if name.lower().startswith(prefix):
            result.append((name, insert, None, cursor_offset))
    return result


def get_bracket_suggestions(
    prefix: str,
    content: str,
    base_dir: Path | None,
    current_filename: str | None = None,
) -> list[tuple[str, str, str | None, int | None]]:
    """返回 [ 补全。格式 (display, insert, notes_str, cursor_offset)。define 项用 notes_str 显示来源文件"""
    prefix_lower = prefix.lower().strip()
    result: list[tuple[str, str, str | None, int | None]] = []
    for name in BRACKET_INSTRUMENTS:
        if name.lower().startswith(prefix_lower):
            result.append((name, f"[{name}]", None, None))
    for name in BRACKET_NOTATIONS:
        if name.lower().startswith(prefix_lower):
            if name in BRACKET_NOTATIONS_WITH_SCOPE:
                result.append((name, f"[{name}]()", None, 1))
            else:
                result.append((name, f"[{name}]", None, None))
    defines, name_to_source = _get_defines_with_sources(content, base_dir, current_filename)
    for name in sorted(defines):
        if name.lower().startswith(prefix_lower) and not any(r[0] == name for r in result):
            source = name_to_source.get(name)
            notes_str = f"来自 {source}" if source else None
            result.append((name, f"[{name}]", notes_str, None))
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
        on_close: Callable[[], None] | None = None,
    ):
        self.text_widget = text_widget
        self.suggestions = suggestions
        self.trigger_pos = trigger_pos
        self.prefix = prefix
        self.on_select = on_select
        self.on_close = on_close
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
            if self.on_close:
                self.on_close()
            self.popup.destroy()
        except tk.TclError:
            pass


def _is_inside_unclosed_bracket(text_before_cursor: str) -> bool:
    """光标前是否在未闭合的方括号内（[ 多于 ]）"""
    open_count = text_before_cursor.count("[") - text_before_cursor.count("]")
    return open_count > 0


def _get_chord_prefix_before_cursor(text_before_cursor: str) -> tuple[int, str]:
    """
    从光标前提取和弦前缀（字母、数字、#、b、/ 组成的连续串）。
    返回 (start_offset, prefix)。
    """
    i = len(text_before_cursor) - 1
    while i >= 0:
        c = text_before_cursor[i]
        if c in " \t\n|[](){}":
            break
        if not (c.isalnum() or c in "#b/"):
            break
        i -= 1
    prefix = text_before_cursor[i + 1 :]
    return i + 1, prefix


def get_chord_completion_context(
    content: str, cursor_pos: int
) -> tuple[int, str, bool] | None:
    """
    若光标前可触发和弦补全，返回 (trigger_pos, prefix, inside_brackets)；否则 None。
    支持两种位置：(1) 不在方括号内，插入 [chord]；(2) 在 [ 后，插入 chord（方括号已存在）。
    """
    before = content[:cursor_pos]
    start, prefix = _get_chord_prefix_before_cursor(before)
    if not prefix or not prefix[0].isalpha():
        return None
    inside_brackets = _is_inside_unclosed_bracket(before)
    return (start, prefix, inside_brackets)


class ChordAutocompletePopup:
    """
    统一补全弹窗：前缀正常、后缀灰色、可选 notes_str（灰色小字）。
    用于 \、[ 及和弦补全。suggestions: (display, insert, notes_str, cursor_offset)。
    """

    def __init__(
        self,
        parent: tk.Widget,
        text_widget: tk.Text,
        suggestions: list[tuple[str, str, str | None, int | None]],
        trigger_pos: int,
        prefix: str,
        on_select: Callable[[str, int | None], None],
        on_close: Callable[[], None] | None = None,
    ):
        self.text_widget = text_widget
        self.suggestions = suggestions
        self.trigger_pos = trigger_pos
        self.prefix = prefix
        self.on_select = on_select
        self.on_close = on_close
        self.selected_index = 0

        self.popup = tk.Toplevel(parent)
        self.popup.wm_overrideredirect(True)
        self.popup.wm_geometry("+0+0")
        self.popup.configure(bg="#f0f0f0")

        # 用 Text 实现：和弦符号 + 将插入的音（灰色小字）
        self.text = tk.Text(
            self.popup,
            height=min(10, len(suggestions)),
            width=48,
            font=("", 11),
            wrap=tk.NONE,
            cursor="arrow",
            highlightthickness=0,
            bg="#fafafa",
            fg="#333",
        )
        self.text.tag_configure("insert_part", foreground="#888", font=("", 9))
        self.text.tag_configure("notes_part", foreground="#666", font=("", 9))
        self.text.bind("<Key>", lambda e: "break")  # 禁止编辑，但保留焦点与点击
        scroll = tk.Scrollbar(self.popup, command=self.text.yview)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.configure(yscrollcommand=scroll.set)

        self._fill_text()
        self._place_near_cursor()

        self.text.bind("<Return>", self._on_enter)
        self.text.bind("<Escape>", self._on_escape)
        self.text.bind("<Up>", self._on_up)
        self.text.bind("<Down>", self._on_down)
        self.text.bind("<Button-1>", self._on_click)

    def _fill_text(self):
        self.text.delete("1.0", tk.END)
        pl = self.prefix.lower().replace("°", "o")
        for item in self.suggestions:
            display = item[0]
            notes_str = item[2] if len(item) > 2 else None
            display_norm = display.lower().replace("°", "o")
            if display_norm.startswith(pl):
                prefix_len = len(self.prefix)
                normal_part = display[: min(prefix_len, len(display))]
                insert_part = display[min(prefix_len, len(display)) :]
            else:
                normal_part = ""
                insert_part = display
            self.text.insert(tk.END, normal_part)
            self.text.insert(tk.END, insert_part, "insert_part")
            if notes_str:
                self.text.insert(tk.END, "  →  ", "insert_part")
                self.text.insert(tk.END, notes_str, "notes_part")
            self.text.insert(tk.END, "\n")

    def _place_near_cursor(self):
        self.text_widget.update_idletasks()
        idx = self.text_widget.index("insert")
        self.text_widget.see(idx)
        bbox = self.text_widget.bbox(idx)
        if not bbox:
            return
        x, y, w, h = bbox
        root_x = self.text_widget.winfo_rootx() + x
        root_y = self.text_widget.winfo_rooty() + y + h + 1
        self.popup.wm_geometry(f"+{root_x}+{root_y}")

    def _on_enter(self, event=None):
        if 0 <= self.selected_index < len(self.suggestions):
            item = self.suggestions[self.selected_index]
            cursor_offset = item[3] if len(item) > 3 else None
            self.close()
            self.on_select(item[1], cursor_offset)
        return "break"

    def _on_escape(self, event=None):
        self.close()
        return "break"

    def _on_up(self, event=None):
        self.selected_index = max(0, self.selected_index - 1)
        self._update_selection()
        return "break"

    def _on_down(self, event=None):
        self.selected_index = min(len(self.suggestions) - 1, self.selected_index + 1)
        self._update_selection()
        return "break"

    def _update_selection(self):
        self.text.tag_remove("sel", "1.0", tk.END)
        line_start = f"{self.selected_index + 1}.0"
        line_end = f"{self.selected_index + 1}.end"
        self.text.tag_add("sel", line_start, line_end)
        self.text.see(line_start)

    def _on_click(self, event=None):
        # 根据点击位置计算行号
        idx = self.text.index(f"@{event.x},{event.y}")
        line = int(idx.split(".")[0])
        if 1 <= line <= len(self.suggestions):
            self.selected_index = line - 1
            self._on_enter()
        return "break"

    def update_suggestions(
        self, suggestions: list[tuple[str, str, str | None, int | None]], prefix: str
    ):
        self.suggestions = suggestions
        self.prefix = prefix
        self.selected_index = 0
        self.text.configure(height=min(10, len(suggestions)))
        self.text.delete("1.0", tk.END)
        pl = prefix.lower().replace("°", "o")
        for item in suggestions:
            display = item[0]
            notes_str = item[2] if len(item) > 2 else None
            display_norm = display.lower().replace("°", "o")
            if display_norm.startswith(pl):
                prefix_len = len(prefix)
                normal_part = display[: min(prefix_len, len(display))]
                insert_part = display[min(prefix_len, len(display)) :]
            else:
                normal_part = ""
                insert_part = display
            self.text.insert(tk.END, normal_part)
            self.text.insert(tk.END, insert_part, "insert_part")
            if notes_str:
                self.text.insert(tk.END, "  →  ", "insert_part")
                self.text.insert(tk.END, notes_str, "notes_part")
            self.text.insert(tk.END, "\n")
        if suggestions:
            self._update_selection()

    def close(self):
        try:
            if self.on_close:
                self.on_close()
            self.popup.destroy()
        except tk.TclError:
            pass
