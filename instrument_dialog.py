"""
乐器面板对话框：展示 sound_library 中所有音色、音域，并提供试听。
"""
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from pathlib import Path

from instrument_registry import (
    get_all_instruments,
    can_play_note,
    can_play_chord,
    select_guitar_strings_for_chord,
    midi_to_note_name,
    parse_note_or_chord_input,
)
from sound_loader import load_sound_library

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
}


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


def show_instrument_dialog(parent: tk.Tk) -> None:
    """打开乐器面板对话框"""
    dlg = tk.Toplevel(parent)
    dlg.title("乐器面板")
    dlg.transient(parent)
    dlg.geometry("720x520")
    dlg.minsize(500, 400)

    main = ttk.Frame(dlg, padding=10)
    main.pack(fill=tk.BOTH, expand=True)

    # 标题
    ttk.Label(main, text="音色库与音域", font=("", 12, "bold")).pack(anchor=tk.W)

    # 乐器列表（Treeview）
    columns = ("instrument", "range", "min_midi", "max_midi")
    tree = ttk.Treeview(main, columns=columns, show="headings", height=14, selectmode="browse")
    tree.heading("instrument", text="乐器")
    tree.heading("range", text="音域")
    tree.heading("min_midi", text="最低 MIDI")
    tree.heading("max_midi", text="最高 MIDI")
    tree.column("instrument", width=180)
    tree.column("range", width=120)
    tree.column("min_midi", width=80)
    tree.column("max_midi", width=80)
    tree.pack(fill=tk.BOTH, expand=True, pady=(5, 10))
    scroll = ttk.Scrollbar(main, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)

    # 填充数据
    instruments = get_all_instruments()
    # 按显示顺序：先普通乐器（按音域从低到高），再 guitar 汇总，最后 guitar 各弦
    ordinary = [n for n in instruments if not n.startswith("guitar")]
    ordinary.sort(key=lambda n: (instruments[n]["min_midi"], n))
    display_order = ordinary.copy()
    if "guitar" in instruments:
        display_order.append("guitar")
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

    # 试听按钮行
    preview_frame = ttk.Frame(main)
    preview_frame.pack(fill=tk.X, pady=(0, 5))

    ttk.Label(preview_frame, text="试听:").pack(side=tk.LEFT, padx=(0, 5))
    midi_var = tk.StringVar(value="C4")
    midi_entry = ttk.Entry(preview_frame, textvariable=midi_var, width=8)
    midi_entry.pack(side=tk.LEFT, padx=(0, 5))
    ttk.Label(preview_frame, text="(MIDI 或音名如 C4)").pack(side=tk.LEFT, padx=(0, 5))

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
            from instrument_registry import select_guitar_string_for_note
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

        def _run():
            _play_preview(path, midi)

        threading.Thread(target=_run, daemon=True).start()

    ttk.Button(preview_frame, text="▶ 试听", command=_on_preview).pack(side=tk.LEFT, padx=(0, 15))

    # 可弹判断测试
    ttk.Label(preview_frame, text="可弹判断:").pack(side=tk.LEFT, padx=(0, 5))
    test_var = tk.StringVar(value="C4")
    test_entry = ttk.Entry(preview_frame, textvariable=test_var, width=14)
    test_entry.pack(side=tk.LEFT, padx=(0, 5))
    ttk.Label(preview_frame, text="(单音或和弦如 C4/E4/G4 或 60/64/67)").pack(side=tk.LEFT, padx=(0, 5))

    result_label = ttk.Label(preview_frame, text="", foreground="gray")
    result_label.pack(side=tk.LEFT, padx=(0, 5))

    def _on_check():
        raw = test_var.get().strip()
        if not raw:
            result_label.config(text="")
            return
        midis = parse_note_or_chord_input(raw)
        if midis is None:
            result_label.config(text="输入格式错误（支持 MIDI 或音名如 C4、C#4）", foreground="red")
            return

        sel = tree.selection()
        if not sel:
            result_label.config(text="请先选择乐器", foreground="orange")
            return
        item = tree.item(sel[0])
        tags = item.get("tags", ())
        inst_id = tags[0] if tags else ""
        if len(midis) > 1:
            check = can_play_chord(inst_id, midis)
        else:
            check = can_play_note(inst_id, midis[0])

        if check:
            result_label.config(text="✓ 可弹", foreground="green")
            if inst_id == "guitar" and len(midis) > 1:
                assign = select_guitar_strings_for_chord(midis)
                if assign:
                    detail = " ".join(f"{midi_to_note_name(m)}→{s}" for m, s in assign)
                    result_label.config(text=f"✓ 可弹 ({detail})", foreground="green")
        else:
            result_label.config(text="✗ 不可弹", foreground="red")

    ttk.Button(preview_frame, text="检查", command=_on_check).pack(side=tk.LEFT, padx=(0, 5))

    # 底部说明
    hint = (
        "试听：选择乐器后输入 MIDI(如 60)或音名(如 C4、C#4)点击试听。"
        "可弹判断：输入单音或和弦(如 C4/E4/G4 或 60/64/67)后点击检查。"
        "吉他会根据音高和和弦自动选择弦。"
    )
    ttk.Label(main, text=hint, font=("", 9), foreground="gray").pack(anchor=tk.W)

    dlg.geometry(f"+{parent.winfo_rootx() + 80}+{parent.winfo_rooty() + 60}")
