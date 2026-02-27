"""
伴奏辅助插入面板
- 1音、2音、3音、4音：和弦中按音高从低到高的第 1/2/3/4 个音（如大三、小三、dim、aug 等）
- 时值记号：- 延长一拍，_ 减半，~ 连音线
- 同数量和弦可配置多种伴奏类型，生成时轮流使用
- 伴奏调性：相对旋律的偏移（0=同调，-12=低八度等）
"""
import sys
from pathlib import Path as _Path

# 确保项目根目录在 sys.path 中（解决直接运行或 IDE 调试时的 ModuleNotFoundError）
_root = _Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path  # 使用标准 Path，_Path 仅用于计算 _root
from typing import Callable, Optional

from src.utils.accompaniment import (
    load_accompaniment,
    save_accompaniment,
    parse_accompaniment_pattern,
)
from src.utils.i18n import _


def show_accompaniment_panel(
    parent: tk.Tk,
    workspace_root: Optional[Path],
    current_filename: Optional[str],
    base_dir: Optional[Path],
    get_content: Callable[[], str],
    on_insert: Callable[[str], None],
) -> None:
    """
    显示伴奏辅助面板。
    get_content: 获取当前编辑器内容
    on_insert: 插入生成的伴奏行
    """
    if not workspace_root or not workspace_root.is_dir():
        messagebox.showwarning(_("伴奏辅助"), _("请先打开工作区"), parent=parent)
        return
    if not current_filename:
        messagebox.showwarning(_("伴奏辅助"), _("请先保存当前文件到工作区"), parent=parent)
        return

    config = load_accompaniment(workspace_root, current_filename)
    patterns_3 = config.get("patterns_3", ["1 2 3"])
    patterns_4 = config.get("patterns_4", ["1 2 3 4"])
    tonality = config.get("tonality", "0")
    if not isinstance(patterns_3, list):
        patterns_3 = [patterns_3] if patterns_3 else ["1 2 3"]
    if not isinstance(patterns_4, list):
        patterns_4 = [patterns_4] if patterns_4 else ["1 2 3 4"]

    dlg = tk.Toplevel(parent)
    dlg.title(_("伴奏辅助插入"))
    dlg.transient(parent)
    dlg.geometry("560x380")
    dlg.resizable(True, True)

    main = ttk.Frame(dlg, padding=15)
    main.pack(fill=tk.BOTH, expand=True)

    ttk.Label(main, text=_("用 1音、2音、3音、4音 表示和弦中按音高从低到高的各音")).pack(anchor=tk.W)
    ttk.Label(main, text=_("时值：- 延长一拍  _ 减半  ~ 连音线"), font=("", 9), foreground="gray").pack(anchor=tk.W)

    ttk.Separator(main, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

    # 伴奏调性
    ton_frame = ttk.Frame(main)
    ton_frame.pack(fill=tk.X, pady=(0, 8))
    ttk.Label(ton_frame, text=_("伴奏调性（相对旋律）：")).pack(side=tk.LEFT, padx=(0, 5))
    ton_entry = ttk.Entry(ton_frame, width=12)
    ton_entry.pack(side=tk.LEFT)
    ton_entry.insert(0, tonality)
    ttk.Label(ton_frame, text=_("（0=同调，-12=低八度，7=高五度）"), font=("", 9), foreground="gray").pack(side=tk.LEFT, padx=(5, 0))

    ttk.Separator(main, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

    # 三音和弦 - 多种模式
    ttk.Label(main, text=_("三音和弦模式（可添加多种，生成时轮流使用）：")).pack(anchor=tk.W)
    list_frame_3 = ttk.Frame(main)
    list_frame_3.pack(fill=tk.X, pady=(2, 2))
    listbox_3 = tk.Listbox(list_frame_3, height=4, width=50, font=("Consolas", 10))
    listbox_3.pack(side=tk.LEFT, fill=tk.X, expand=True)
    for p in patterns_3:
        listbox_3.insert(tk.END, p)
    btn_frame_3 = ttk.Frame(list_frame_3)
    btn_frame_3.pack(side=tk.LEFT, padx=(5, 0))
    entry_3 = ttk.Entry(main, width=50)
    entry_3.pack(fill=tk.X, pady=(2, 2))
    entry_3.insert(0, "1 2 3")

    def _add_pattern_3():
        s = entry_3.get().strip()
        if s:
            listbox_3.insert(tk.END, s)
            entry_3.delete(0, tk.END)
            entry_3.insert(0, "1 2 3")
            _save_config()

    def _del_pattern_3():
        sel = listbox_3.curselection()
        if sel:
            listbox_3.delete(sel[0])
            _save_config()

    ttk.Button(btn_frame_3, text=_("添加"), command=_add_pattern_3, width=6).pack(fill=tk.X, pady=1)
    ttk.Button(btn_frame_3, text=_("删除"), command=_del_pattern_3, width=6).pack(fill=tk.X, pady=1)

    ttk.Separator(main, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

    # 四音和弦 - 多种模式
    ttk.Label(main, text=_("四音和弦模式（可添加多种，生成时轮流使用）：")).pack(anchor=tk.W)
    list_frame_4 = ttk.Frame(main)
    list_frame_4.pack(fill=tk.X, pady=(2, 2))
    listbox_4 = tk.Listbox(list_frame_4, height=4, width=50, font=("Consolas", 10))
    listbox_4.pack(side=tk.LEFT, fill=tk.X, expand=True)
    for p in patterns_4:
        listbox_4.insert(tk.END, p)
    btn_frame_4 = ttk.Frame(list_frame_4)
    btn_frame_4.pack(side=tk.LEFT, padx=(5, 0))
    entry_4 = ttk.Entry(main, width=50)
    entry_4.pack(fill=tk.X, pady=(2, 2))
    entry_4.insert(0, "1 2 3 4")

    def _add_pattern_4():
        s = entry_4.get().strip()
        if s:
            listbox_4.insert(tk.END, s)
            entry_4.delete(0, tk.END)
            entry_4.insert(0, "1 2 3 4")
            _save_config()

    def _del_pattern_4():
        sel = listbox_4.curselection()
        if sel:
            listbox_4.delete(sel[0])
            _save_config()

    ttk.Button(btn_frame_4, text=_("添加"), command=_add_pattern_4, width=6).pack(fill=tk.X, pady=1)
    ttk.Button(btn_frame_4, text=_("删除"), command=_del_pattern_4, width=6).pack(fill=tk.X, pady=1)

    ttk.Label(main, text=_("示例：1 2 3 4 | 1_ 2_ 3_ 4_ | 1- 2 3"), font=("", 9), foreground="gray").pack(anchor=tk.W)

    def _get_patterns():
        p3 = [listbox_3.get(i) for i in range(listbox_3.size())]
        p4 = [listbox_4.get(i) for i in range(listbox_4.size())]
        return p3, p4

    def _save_config():
        p3, p4 = _get_patterns()
        ton = ton_entry.get().strip()
        cfg = {"tonality": ton or "0"}
        if p3:
            cfg["patterns_3"] = p3
        if p4:
            cfg["patterns_4"] = p4
        save_accompaniment(workspace_root, current_filename, cfg)

    ton_entry.bind("<KeyRelease>", lambda e: _save_config())
    dlg.geometry(f"+{parent.winfo_rootx() + 80}+{parent.winfo_rooty() + 80}")


def _generate_accompaniment_line(
    content: str,
    patterns_3: list[str],
    patterns_4: list[str],
    accompaniment_tonality: str,
    base_dir: Path,
) -> tuple[Optional[str], Optional[str]]:
    """
    根据旋律中的和弦生成伴奏行（不插入）。
    返回 (生成的伴奏行, 错误信息)，成功时错误为 None。
    """
    from src.core.parser import parse, _tonality_to_semitones
    from src.core.preprocessor import expand_imports
    from src.utils.chord_utils import get_tonality_offset, find_chords_in_range
    from src.utils.chord_symbols import find_chord_symbol_tokens, parse_chord_symbol
    from src.utils.accompaniment import parse_accompaniment_pattern, chord_parts_to_sorted_notation

    if not patterns_3 and not patterns_4:
        return None, _("请至少添加一个三音或四音和弦模式")

    try:
        expanded = expand_imports(content, base_dir)
    except Exception:
        expanded = content

    try:
        parse(expanded)
    except Exception as e:
        return None, _("解析失败：{e}").format(e=e)

    melody_tonality = get_tonality_offset(content)
    try:
        acc_tonality_offset = _tonality_to_semitones(accompaniment_tonality.strip() or "0")
    except Exception:
        acc_tonality_offset = 0
    total_tonality = melody_tonality + acc_tonality_offset

    notes_3_list = [parse_accompaniment_pattern(p or "1 2 3", 1.0) for p in patterns_3]
    notes_4_list = [parse_accompaniment_pattern(p or "1 2 3 4", 1.0) for p in patterns_4]
    idx_3, idx_4 = 0, 0

    # 收集斜杠和弦 1/3/5 与 [V7]、[G7] 等和弦符号，按位置排序
    chord_items: list[tuple[int, int, list[str]]] = []
    for c_start, c_end, tok in find_chords_in_range(expanded, 0, len(expanded)):
        parts = [p.strip() for p in tok.rstrip("_").split("/") if p.strip()]
        if len(parts) < 2 or len(parts) > 4:
            continue
        if not all(any(c in "1234567" for c in p.lstrip("~.#b^")) for p in parts):
            continue
        chord_items.append((c_start, c_end, parts))
    for s_start, s_end, symbol in find_chord_symbol_tokens(expanded, 0, len(expanded)):
        parts = parse_chord_symbol(symbol, total_tonality)
        if parts and 2 <= len(parts) <= 4:
            chord_items.append((s_start, s_end, parts))
    chord_items.sort(key=lambda x: x[0])

    acc_notes: list[str] = []
    for _cstart, _cend, parts in chord_items:
        n = len(parts)
        if n == 4:
            pat_list = notes_4_list
            idx = idx_4
            idx_4 = (idx_4 + 1) % len(pat_list) if pat_list else 0
        else:
            pat_list = notes_3_list
            idx = idx_3
            idx_3 = (idx_3 + 1) % len(pat_list) if pat_list else 0
        if not pat_list:
            continue
        pat = pat_list[idx]
        sorted_parts = chord_parts_to_sorted_notation(parts, total_tonality)
        for pn in pat:
            if pn.position < len(sorted_parts):
                s = sorted_parts[pn.position]
                if pn.duration_beats <= 0.5:
                    s += "_"
                elif pn.duration_beats >= 2:
                    s += "-" * int(pn.duration_beats - 1)
                if pn.tied_to_next:
                    s = "~" + s
                acc_notes.append(s)

    if not acc_notes:
        return None, _("未在旋律中找到可伴奏的和弦（需 2–4 音的和弦，如 1/3/5 或 [V7]、[G7]）")

    bar_size = min(8, max(4, len(acc_notes) // 2))
    bars = []
    for i in range(0, len(acc_notes), bar_size):
        chunk = acc_notes[i : i + bar_size]
        bars.append("|" + " ".join(chunk) + "|")
    line = "& [8vb][pp]" + "".join(bars)
    return line, None


def _generate_and_insert_accompaniment(
    content: str,
    patterns_3: list[str],
    patterns_4: list[str],
    accompaniment_tonality: str,
    base_dir: Path,
    on_insert: Callable[[str], None],
) -> Optional[str]:
    """
    根据旋律中的和弦生成伴奏行并插入。
    返回错误信息，成功则返回 None。
    """
    line, err = _generate_accompaniment_line(
        content, patterns_3, patterns_4, accompaniment_tonality, base_dir
    )
    if err:
        return err
    on_insert("\n" + line + "\n")
    return None
