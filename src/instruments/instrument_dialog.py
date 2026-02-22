"""
乐器面板对话框：展示 sound_library 中所有音色、音域，并提供试听。
左右分栏：左选乐器+MIDI范围，右快捷插入。鼓声部插入简谱格式（.1 .2 .3 等）。
"""
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from pathlib import Path
from typing import Callable

from src.instruments.instrument_registry import (
    get_all_instruments,
    can_play_note,
    can_play_chord,
    select_guitar_strings_for_chord,
    midi_to_note_name,
    midi_to_simplified_notation,
    parse_note_or_chord_input,
)
from src.audio.sound_loader import load_sound_library

# 乐器显示名
INSTRUMENT_DISPLAY_NAMES = {
    "grand_piano": "钢琴",
    "violin": "小提琴",
    "cello": "大提琴",
    "trumpet": "小号",
    "clarinet": "单簧管",
    "oboe": "双簧管",
    "alto_sax": "中音萨克斯",
    "tenor_sax": "次中音萨克斯",
    "bass": "贝斯",
    "guitar": "吉他（四弦）",
    "guitar_string_a": "吉他弦 A",
    "guitar_string_d": "吉他弦 D",
    "guitar_string_g": "吉他弦 G",
    "guitar_string_b": "吉他弦 B",
    "drums": "鼓",
    "guitar_electric": "电吉他",
    "bass_electric": "电贝斯",
}

# GM 鼓表：MIDI -> (名称, 象声词)
DRUM_MAP: list[tuple[int, str, str]] = [
    (28, "Slap（鼓棒）", "啪"),
    (31, "Sticks（鼓棒）", "哒"),
    (35, "Acoustic Bass Drum", "咚"),
    (36, "Bass Drum 1", "咚"),
    (37, "Side Stick（敲边）", "咔"),
    (38, "Acoustic Snare", "哒"),
    (39, "Hand Clap", "啪"),
    (40, "Electric Snare", "哒"),
    (41, "Low Floor Tom", "咚"),
    (42, "Closed Hi-Hat", "哧"),
    (43, "High Floor Tom", "咚"),
    (44, "Pedal Hi-Hat", "哧"),
    (45, "Low Tom", "咚"),
    (46, "Open Hi-Hat", "嚓"),
    (47, "Low-Mid Tom", "咚"),
    (48, "Hi-Mid Tom", "咚"),
    (49, "Crash Cymbal 1", "锵"),
    (50, "High Tom", "咚"),
    (51, "Ride Cymbal 1", "叮"),
    (52, "Chinese Cymbal", "锵"),
    (53, "Ride Bell", "叮"),
    (55, "Splash Cymbal", "哗"),
    (57, "Crash Cymbal 2", "锵"),
    (59, "Ride Cymbal 2", "叮"),
]


def _play_preview(library_path: str, midi: int, sample_rate: int = 44100) -> None:
    """在后台线程播放单音试听"""
    try:
        import numpy as np
        import soundfile as sf
        import sounddevice as sd

        mapping = load_sound_library(library_path)
        path = mapping.get(midi)
        if not path or not Path(path).exists():
            return
        data, sr = sf.read(path, dtype="float32")
        if len(data.shape) > 1:
            data = data.mean(axis=1)
        if sr != sample_rate:
            ratio = sample_rate / sr
            new_len = int(len(data) * ratio)
            indices = np.linspace(0, len(data) - 1, new_len)
            data = np.interp(indices, np.arange(len(data)), data)
        audio = np.column_stack([data, data])
        sd.play(audio, sample_rate)
        sd.wait()
    except Exception:
        pass


def show_instrument_dialog(
    parent: tk.Tk,
    insert_callback: Callable[[str], None] | None = None,
    tonality_offset: int = 0,
) -> None:
    """打开乐器面板对话框。insert_callback(text) 用于将文本插入编辑器光标处。tonality_offset 用于鼓声部简谱转换。"""
    dlg = tk.Toplevel(parent)
    dlg.title("乐器面板")
    dlg.transient(parent)
    dlg.geometry("880x520")
    dlg.minsize(600, 400)

    main = ttk.Frame(dlg, padding=10)
    main.pack(fill=tk.BOTH, expand=True)

    # 左右分栏
    paned = ttk.PanedWindow(main, orient=tk.HORIZONTAL)
    paned.pack(fill=tk.BOTH, expand=True)

    # ========== 左侧：乐器选择 + MIDI 范围 ==========
    left = ttk.Frame(paned, padding=(0, 5))
    paned.add(left, weight=1)

    ttk.Label(left, text="乐器与音域", font=("", 11, "bold")).pack(anchor=tk.W)

    columns = ("instrument", "range", "min_midi", "max_midi")
    tree = ttk.Treeview(left, columns=columns, show="headings", height=12, selectmode="browse")
    tree.heading("instrument", text="乐器")
    tree.heading("range", text="音域")
    tree.heading("min_midi", text="最低")
    tree.heading("max_midi", text="最高")
    tree.column("instrument", width=140)
    tree.column("range", width=100)
    tree.column("min_midi", width=50)
    tree.column("max_midi", width=50)
    tree.pack(fill=tk.BOTH, expand=True, pady=(5, 8))

    instruments = get_all_instruments()
    ordinary = [n for n in instruments if not n.startswith("guitar")]
    ordinary.sort(key=lambda n: (instruments[n]["min_midi"], n))
    display_order = ordinary.copy()
    if "guitar" in instruments:
        display_order.append("guitar")
    if "guitar_electric" in instruments:
        display_order.append("guitar_electric")
    for s in ("a", "d", "g", "b"):
        key = f"guitar_string_{s}"
        if key in instruments:
            display_order.append(key)

    for name in display_order:
        if name not in instruments:
            continue
        info = instruments[name]
        lo, hi = info["min_midi"], info["max_midi"]
        range_str = f"{midi_to_note_name(lo)} - {midi_to_note_name(hi)}"
        display_name = INSTRUMENT_DISPLAY_NAMES.get(name, name)
        tree.insert("", tk.END, values=(display_name, range_str, lo, hi), tags=(name,))

    # 试听、可弹（两行，降低横向占地）
    preview_frame = ttk.Frame(left)
    preview_frame.pack(fill=tk.X, pady=(5, 0))
    row1 = ttk.Frame(preview_frame)
    row1.pack(fill=tk.X, pady=(0, 2))
    ttk.Label(row1, text="试听:").pack(side=tk.LEFT, padx=(0, 5))
    midi_var = tk.StringVar(value="C4")
    ttk.Entry(row1, textvariable=midi_var, width=8).pack(side=tk.LEFT, padx=(0, 5))

    def _on_preview():
        parsed = parse_note_or_chord_input(midi_var.get())
        if not parsed or len(parsed) != 1:
            messagebox.showwarning("试听", "请输入单个 MIDI(21-108) 或音名(如 C4)", parent=dlg)
            return
        midi = parsed[0]
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("试听", "请先选择乐器", parent=dlg)
            return
        item = tree.item(sel[0])
        tags = item.get("tags", ())
        if not tags:
            return
        inst_id = tags[0]
        if inst_id not in instruments:
            return
        info = instruments[inst_id]
        path = info["path"]
        if inst_id == "guitar":
            from src.instruments.instrument_registry import select_guitar_string_for_note
            sid = select_guitar_string_for_note(midi)
            if sid:
                key = f"guitar_string_{sid}"
                if key in instruments:
                    path = instruments[key]["path"]
            else:
                messagebox.showwarning("试听", f"吉他无法弹奏 MIDI {midi}", parent=dlg)
                return
        if not can_play_note(inst_id if inst_id != "guitar" else "guitar", midi):
            messagebox.showwarning("试听", f"该乐器音域不包含 MIDI {midi}", parent=dlg)
            return
        threading.Thread(target=lambda: _play_preview(path, midi), daemon=True).start()

    ttk.Button(row1, text="▶ 试听", command=_on_preview).pack(side=tk.LEFT)

    row2 = ttk.Frame(preview_frame)
    row2.pack(fill=tk.X)
    ttk.Label(row2, text="可弹:").pack(side=tk.LEFT, padx=(0, 5))
    test_var = tk.StringVar(value="C4")
    ttk.Entry(row2, textvariable=test_var, width=10).pack(side=tk.LEFT, padx=(0, 5))
    result_label = ttk.Label(row2, text="", foreground="gray")
    result_label.pack(side=tk.LEFT, padx=(0, 5))

    def _on_check():
        raw = test_var.get().strip()
        if not raw:
            result_label.config(text="")
            return
        midis = parse_note_or_chord_input(raw)
        if midis is None:
            result_label.config(text="格式错误", foreground="red")
            return
        sel = tree.selection()
        if not sel:
            result_label.config(text="请选乐器", foreground="orange")
            return
        inst_id = tree.item(sel[0]).get("tags", ("",))[0]
        check = can_play_chord(inst_id, midis) if len(midis) > 1 else can_play_note(inst_id, midis[0])
        result_label.config(text="✓ 可弹" if check else "✗ 不可弹", foreground="green" if check else "red")

    ttk.Button(row2, text="检查", command=_on_check).pack(side=tk.LEFT)

    # ========== 右侧：快捷插入 ==========
    right = ttk.LabelFrame(paned, text="快捷插入", padding=8)
    paned.add(right, weight=1)

    def _do_insert(text: str):
        if insert_callback and text:
            insert_callback(text)

    preview_only_var = tk.BooleanVar(value=False)

    def _update_right():
        for w in right.winfo_children():
            w.destroy()
        sel = tree.selection()
        inst_id = ""
        if sel:
            tags = tree.item(sel[0]).get("tags", ())
            inst_id = tags[0] if tags else ""
        if not inst_id:
            ttk.Label(right, text="请先在左侧选择乐器", foreground="gray").pack(expand=True)
            return
        if inst_id == "drums":
            # 鼓：插入简谱格式，用 Frame 模拟按钮以支持两行及可调高度（mac 原生按钮高度固定）
            row_top = ttk.Frame(right)
            row_top.pack(fill=tk.X)
            ttk.Label(row_top, text="点击插入鼓音（简谱）:", font=("", 9)).pack(side=tk.LEFT, padx=(0, 10))
            ttk.Checkbutton(row_top, text="仅试听", variable=preview_only_var).pack(side=tk.LEFT)
            btn_frame = ttk.Frame(right)
            btn_frame.pack(fill=tk.BOTH, expand=True, pady=5)
            for c in range(4):
                btn_frame.columnconfigure(c, uniform="drum", minsize=100)
            _nrows = (len(DRUM_MAP) + 3) // 4
            for r in range(_nrows):
                btn_frame.rowconfigure(r, uniform="drum_row", minsize=48)
            _bg, _bg_hover, _border = "#f5f5f5", "#e8ecf0", "#d0d4d8"
            for i, (midi, name, onom) in enumerate(DRUM_MAP):
                row, col = i // 4, i % 4
                simp = midi_to_simplified_notation(midi, 0)  # 鼓不受 tonality 控制
                w = tk.Frame(btn_frame, bg=_bg, relief="flat", bd=0, cursor="hand2", padx=6, pady=5,
                             highlightbackground=_border, highlightthickness=1)
                l1 = tk.Label(w, text=name, font=("", 10), wraplength=95, bg=_bg, fg="#333", cursor="hand2")
                l2 = tk.Label(w, text=f"{simp}  |  {onom}", font=("", 10, "bold"), bg=_bg, fg="#5a6c7d", cursor="hand2")
                l1.pack(anchor=tk.W)
                l2.pack(anchor=tk.W)
                w.grid(row=row, column=col, padx=3, pady=3, sticky=tk.NSEW)

                def _hover_in(ev, f=w, lbg=_bg_hover):
                    f.configure(bg=lbg)
                    for c in f.winfo_children():
                        c.configure(bg=lbg)

                def _hover_out(ev, f=w, lbg=_bg):
                    f.configure(bg=lbg)
                    for c in f.winfo_children():
                        c.configure(bg=lbg)

                def _click(ev, s=simp, m=midi):
                    drums_path = instruments.get("drums", {}).get("path")
                    if drums_path:
                        threading.Thread(target=lambda: _play_preview(drums_path, m), daemon=True).start()
                    if not preview_only_var.get():
                        _do_insert(f"{s} ")
                w.bind("<Enter>", _hover_in)
                w.bind("<Leave>", _hover_out)
                w.bind("<Button-1>", _click)
                l1.bind("<Button-1>", _click)
                l2.bind("<Button-1>", _click)
            ttk.Button(right, text="插入 [drums]", command=lambda: _do_insert("[drums]")).pack(anchor=tk.W, pady=(5, 0))
        else:
            # 非鼓：列表插入乐器标记
            ttk.Label(right, text="点击插入乐器标记:", font=("", 9)).pack(anchor=tk.W)
            btn_frame = ttk.Frame(right)
            btn_frame.pack(fill=tk.BOTH, expand=True, pady=5)
            for c in range(3):
                btn_frame.columnconfigure(c, uniform="inst", minsize=90)
            names = [n for n in display_order if n in instruments]
            for r in range((len(names) + 2) // 3):
                btn_frame.rowconfigure(r, uniform="inst_row", minsize=28)
            for i, name in enumerate(names):
                row, col = i // 3, i % 3
                display_name = INSTRUMENT_DISPLAY_NAMES.get(name, name)
                ttk.Button(
                    btn_frame, text=f"[{display_name}]", width=10,
                    command=lambda n=name: _do_insert(f"[{n}]"),
                ).grid(row=row, column=col, padx=2, pady=2, sticky=tk.NSEW)

    tree.bind("<<TreeviewSelect>>", lambda e: _update_right())
    # 默认选中第一项
    first = tree.get_children()
    if first:
        tree.selection_set(first[0])
        tree.focus(first[0])
    _update_right()

    dlg.geometry(f"+{parent.winfo_rootx() + 80}+{parent.winfo_rooty() + 60}")
