"""
VOICEVOX 音色选择对话框
左侧：扁平音色列表，点击即试听
右侧：全身照背景 + 利用規約（同角色不同风格照片不同）
"""
import base64
import io
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
from pathlib import Path
from typing import Callable, Optional

from voicevox_client import (
    fetch_speakers,
    fetch_speaker_info,
    fetch_singers,
    clear_singers_cache,
    synthesize_simple,
    resolve_speakers_style_id,
    get_legal_info_for_speaker,
    VOICEVOX_BASE,
)


# 试听用示例文本
PREVIEW_TEXT = "こんにちは、VOICEVOXです。"

# 记忆上次选择的音色
def _voicevox_config_path() -> Path:
    return Path.home() / ".config" / "ascii_choir" / "voicevox_last_style.txt"


def _load_last_style_id() -> Optional[int]:
    try:
        p = _voicevox_config_path()
        if p.exists():
            v = p.read_text(encoding="utf-8").strip()
            return int(v)
    except (ValueError, OSError):
        pass
    return None


def _save_last_style_id(style_id: int) -> None:
    try:
        p = _voicevox_config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(style_id), encoding="utf-8")
    except OSError:
        pass


# 左侧列表大头照尺寸（上中部裁剪），稍大以增加行距
LIST_ICON_SIZE = (44, 44)
# 右侧全身照背景
BG_PORTRAIT_MIN_SIZE = (200, 300)


def _img_data_from_source(data: str, is_url: bool) -> bytes:
    """从 URL 或 base64 获取图片字节"""
    if is_url:
        import urllib.request
        with urllib.request.urlopen(data, timeout=5) as resp:
            return resp.read()
    return base64.b64decode(data)


def _crop_upper_center(img, out_size: tuple[int, int]):
    """裁剪图片上中部作为大头照（头肩区域，避免只露额头）"""
    from PIL import Image
    w, h = img.size
    # 取顶部 55% 高度（头+肩），正方形居中，避免裁得过窄只露额头
    crop_h = int(h * 0.55)
    side = min(w, crop_h)
    left = (w - side) // 2
    cropped = img.crop((left, 0, left + side, crop_h))
    cropped.thumbnail(out_size, Image.Resampling.LANCZOS)
    return cropped


def _load_list_icon(data: str, is_url: bool = False) -> Optional[tk.PhotoImage]:
    """加载列表用大头照：icon 直接缩放；portrait 裁剪上中部"""
    try:
        from PIL import Image, ImageTk
        raw = Image.open(io.BytesIO(_img_data_from_source(data, is_url))).convert("RGBA")
        w, h = raw.size
        # 若已是近似正方形（icon），直接缩放
        if 0.7 <= w / h <= 1.4:
            raw.thumbnail(LIST_ICON_SIZE, Image.Resampling.LANCZOS)
            img = raw
        else:
            img = _crop_upper_center(raw, LIST_ICON_SIZE)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


def _load_portrait_image(data: str, is_url: bool = False, max_size: tuple[int, int] = (96, 96)) -> Optional[tk.PhotoImage]:
    """加载头像/全身照，可指定最大尺寸"""
    try:
        from PIL import Image, ImageTk
        img = Image.open(io.BytesIO(_img_data_from_source(data, is_url))).convert("RGBA")
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


def _load_background_portrait(data: str, is_url: bool, canvas_size: tuple[int, int]) -> Optional[tk.PhotoImage]:
    """加载全身照作为背景，等比缩放完整展示（contain，确保全身可见）"""
    try:
        from PIL import Image, ImageTk
        img = Image.open(io.BytesIO(_img_data_from_source(data, is_url))).convert("RGB")
        cw, ch = canvas_size
        if cw < 10 or ch < 10:
            cw, ch = 350, 480
        iw, ih = img.size
        # contain：缩放至完整放入画布，不裁剪
        scale = min(cw / iw, ch / ih)
        nw, nh = int(iw * scale), int(ih * scale)
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


def _play_wav_bytes(wav_bytes: bytes) -> None:
    """播放 WAV 字节（需在后台线程调用，避免阻塞 UI）"""
    try:
        import io
        import sounddevice as sd
        import soundfile as sf
        data, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        if len(data.shape) > 1:
            data = data.mean(axis=1)
        stereo = data.reshape(-1, 1).repeat(2, axis=1)
        sd.play(stereo, sr)
        sd.wait()
    except ImportError:
        # 保存到临时文件，用系统播放
        tmp = __import__("pathlib").Path(".voicevox_preview.wav")
        tmp.write_bytes(wav_bytes)
        import subprocess
        import sys
        if sys.platform == "darwin":
            subprocess.run(["afplay", str(tmp)], check=False)
        elif sys.platform == "win32":
            subprocess.run(["start", "", str(tmp)], shell=True, check=False)
        else:
            subprocess.run(["aplay", str(tmp)], check=False)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    except Exception as e:
        messagebox.showerror("播放失败", str(e))


class VoiceVoxVoiceDialog(tk.Toplevel):
    """VOICEVOX 音色选择与试听对话框"""

    def __init__(
        self,
        parent: tk.Tk,
        base_url: str = VOICEVOX_BASE,
        get_score_callback: Optional[Callable[[], str]] = None,
        get_current_file_callback: Optional[Callable[[], "Path | None"]] = None,
    ):
        super().__init__(parent)
        self.base_url = base_url
        self.get_score_callback = get_score_callback
        self.get_current_file_callback = get_current_file_callback
        self.title("VOICEVOX 音色选择")
        self.geometry("1050x820")
        self.transient(parent)

        self.speakers_data: list[dict] = []
        self.singers_data: list[dict] = []
        self.voice_map: dict[str, tuple[int, str, str]] = {}  # item_id -> (style_id, speaker_name, speaker_uuid)，style_id 统一为 API 返回的 id
        self.selected_style_id: Optional[int] = None
        self.selected_speaker_name: Optional[str] = None
        self._icon_photos: dict[str, tk.PhotoImage] = {}  # 列表图标引用
        self._bg_photo: Optional[tk.PhotoImage] = None  # 右侧背景
        self._right_canvas: Optional[tk.Canvas] = None

        main = ttk.Frame(self, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main, text="左侧点击音色即试听").pack(anchor=tk.W)

        paned = ttk.PanedWindow(main, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=5)

        # 左侧：扁平音色列表，点击即试听
        left_frame = ttk.LabelFrame(paned, text="音色（点击试听）", padding=5)
        paned.add(left_frame, weight=1)
        _style = ttk.Style()
        _style.configure("Treeview", rowheight=44)
        self.tree = ttk.Treeview(left_frame, height=14, show="tree headings", columns=("name", "sing"), selectmode="browse")
        self.tree.heading("#0", text="")
        self.tree.heading("name", text="角色 - 风格")
        self.tree.heading("sing", text="唱歌")
        self.tree.column("#0", width=50, minwidth=50)
        self.tree.column("name", width=160, minwidth=100)
        self.tree.column("sing", width=44, minwidth=44)
        scroll_l = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_l.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_l.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # 右侧：全身照背景 + 利用規約，右上角显示当前音色 ID
        right_frame = ttk.LabelFrame(paned, text="角色信息・利用規約", padding=5)
        paned.add(right_frame, weight=3)
        self._id_label = ttk.Label(right_frame, text="ID: —", font=("", 11))
        self._id_label.place(relx=1.0, rely=0, anchor=tk.NE, x=-8, y=4)
        self._right_canvas = tk.Canvas(right_frame, bg="#e0e0e0", highlightthickness=0)
        self._right_canvas.pack(fill=tk.BOTH, expand=True)
        text_container = tk.Frame(right_frame, bg="#f8f8f8")
        text_container.place(relx=0.02, rely=0.68, relwidth=0.96, relheight=0.30)
        self.legal_text = scrolledtext.ScrolledText(
            text_container, wrap=tk.WORD, font=("", 10), state=tk.DISABLED, bg="#fafafa"
        )
        self.legal_text.pack(fill=tk.BOTH, expand=True)

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btn_frame, text="复制 TTS 命令", command=self._copy_tts_cmd).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="复制 歌词命令", command=self._copy_lyrics_cmd).pack(side=tk.LEFT, padx=(0, 5))
        if get_score_callback:
            ttk.Button(btn_frame, text="清唱生成", command=self._on_acappella).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="刷新列表", command=self._load_speakers).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="关闭", command=self.destroy).pack(side=tk.RIGHT)

        self._status = ttk.Label(main, text="")
        self._status.pack(anchor=tk.W, pady=(5, 0))

        self._load_speakers()

    def _load_speakers(self) -> None:
        """从 API 加载音色列表"""
        self._status.config(text="正在连接 VOICEVOX 引擎...")
        self.update_idletasks()
        clear_singers_cache()

        def _fetch():
            try:
                speakers_data = fetch_speakers(self.base_url)
                singers_data = fetch_singers(self.base_url)
                self.after(0, lambda: self._apply_speakers(speakers_data, singers_data))
            except Exception as e:
                import traceback
                err_msg, tb_str = str(e), traceback.format_exc()
                self.after(0, lambda: self._on_error(err_msg, tb_str))

        threading.Thread(target=_fetch, daemon=True).start()

    def _apply_speakers(self, data: list[dict], singers: list[dict] | None = None) -> None:
        self.speakers_data = data
        self.singers_data = singers or []
        self.voice_map = {}
        self._icon_photos.clear()
        self.tree.delete(*self.tree.get_children())
        # 以 /singers 为主：统一使用 VOICEVOX 接口返回的 id，歌唱与复制命令直接可用
        use_singers = bool(self.singers_data)
        total = 0
        if use_singers:
            for sp in self.singers_data:
                name = sp.get("name", "?")
                uuid = sp.get("speaker_uuid", "") or sp.get("uuid", "")
                for st in sp.get("styles", []):
                    sid = st.get("id")
                    if sid is None:
                        continue
                    sname = st.get("name", "通常")
                    label = f"{name} - {sname}"
                    item = self.tree.insert("", tk.END, text="", values=(label, "是"))
                    self.voice_map[item] = (sid, name, uuid)
                    total += 1
        else:
            for sp in data:
                name = sp.get("name", "?")
                uuid = sp.get("speaker_uuid", "")
                for st in sp.get("styles", []):
                    sid = st.get("id", 0)
                    sname = st.get("name", "通常")
                    label = f"{name} - {sname}"
                    item = self.tree.insert("", tk.END, text="", values=(label, "否"))
                    self.voice_map[item] = (sid, name, uuid)
                    total += 1
        status_text = f"已加载 {total} 个音色"
        if not use_singers:
            status_text += "（未检测到歌唱角色，请安装波音リツ等歌唱音声库）"
        status_text += "，正在加载头像..."
        self._status.config(text=status_text)
        last_style = _load_last_style_id()
        threading.Thread(target=self._load_all_icons, daemon=True).start()
        # 恢复上次选择的音色（仅展示，不自动试听）
        if last_style is not None:
            for iid, (sid, _, _) in self.voice_map.items():
                if sid == last_style:
                    def _restore(item=iid):
                        self.tree.selection_set(item)
                        self.tree.see(item)
                        self._on_select(skip_preview=True)
                    self.after(200, _restore)
                    break

    def _load_all_icons(self) -> None:
        """后台为每项加载 icon"""
        icon_source = self.singers_data if self.singers_data else self.speakers_data
        for sp in icon_source:
            uuid = sp.get("speaker_uuid", "") or sp.get("uuid", "")
            if not uuid:
                continue
            try:
                for fmt in ("url", "base64"):
                    try:
                        info = fetch_speaker_info(uuid, self.base_url, resource_format=fmt)
                        break
                    except Exception:
                        continue
                else:
                    continue
                style_infos = info.get("style_infos") or []
                portrait = info.get("portrait") or ""
                is_url = fmt == "url"
                for st in sp.get("styles", []):
                    sid = st.get("id", 0)
                    match_id = sid
                    if self.singers_data:
                        resolved = resolve_speakers_style_id(sid, self.base_url)
                        if resolved is not None:
                            match_id = resolved
                    icon = None
                    for si in style_infos:
                        if si.get("id") == match_id and si.get("icon"):
                            ic = si["icon"]
                            iu = is_url and (ic.startswith("http://") or ic.startswith("https://"))
                            icon = _load_list_icon(ic, is_url=iu)
                            break
                    if not icon and portrait:
                        icon = _load_list_icon(portrait, is_url=is_url and (portrait.startswith("http://") or portrait.startswith("https://")))
                    if icon:
                        for iid, (isid, _, cuuid) in self.voice_map.items():
                            if cuuid == uuid and isid == sid:
                                self._icon_photos[iid] = icon
                                self.after(0, lambda x=iid, img=icon: self.tree.item(x, image=img))
                                break
            except Exception:
                pass
        self.after(0, lambda: self._status.config(text=f"已加载 {len(self.voice_map)} 个音色"))

    def _on_error(self, msg: str, tb: str = "") -> None:
        self._status.config(text="")
        try:
            from gui import show_error_detail
            show_error_detail(self, "VOICEVOX 连接失败", msg, tb if tb else None)
        except ImportError:
            messagebox.showerror("VOICEVOX 连接失败", msg + (f"\n\n{tb}" if tb else ""))
        self._status.config(text="连接失败，请确保 voicevox_engine 已启动 (http://localhost:50021)")

    def _on_select(self, event=None, skip_preview: bool = False) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        item_id = sel[0]
        if item_id not in self.voice_map:
            self._id_label.config(text="ID: —")
            self._set_background(None)
            return
        style_id, speaker_name, speaker_uuid = self.voice_map[item_id]
        self.selected_style_id = style_id
        self.selected_speaker_name = speaker_name
        self._id_label.config(text=f"ID: {style_id}")
        _save_last_style_id(style_id)
        info = get_legal_info_for_speaker(speaker_name)
        self.legal_text.config(state=tk.NORMAL)
        self.legal_text.delete(1.0, tk.END)
        self.legal_text.insert(tk.END, info)
        self.legal_text.config(state=tk.DISABLED)
        # 点击即试听（恢复选择时跳过）
        if skip_preview:
            self._status.config(text="")
        else:
            self._status.config(text="正在合成试听...")
        self.update_idletasks()

        if not skip_preview:
            preview_id = style_id
            if self.singers_data:
                resolved = resolve_speakers_style_id(style_id, self.base_url)
                if resolved is not None:
                    preview_id = resolved
            def _synth():
                try:
                    wav = synthesize_simple(PREVIEW_TEXT, preview_id, self.base_url)
                    threading.Thread(target=_play_wav_bytes, args=(wav,), daemon=True).start()
                    self.after(0, lambda: self._status.config(text=""))
                except Exception as e:
                    import traceback
                    err_msg, tb_str = str(e), traceback.format_exc()
                    self.after(0, lambda: self._on_preview_error(err_msg, tb_str))

            threading.Thread(target=_synth, daemon=True).start()
        # 异步加载该风格专属全身照做背景（同角色不同风格照片不同）
        portrait_match_id = style_id
        if self.singers_data:
            resolved = resolve_speakers_style_id(style_id, self.base_url)
            if resolved is not None:
                portrait_match_id = resolved
        def _fetch():
            portrait, is_url = "", False
            for fmt in ("url", "base64"):
                try:
                    inf = fetch_speaker_info(speaker_uuid, self.base_url, resource_format=fmt)
                    # 优先使用当前 style_id 对应的 portrait，同角色不同风格照片不同
                    for si in inf.get("style_infos") or []:
                        if si.get("id") == portrait_match_id and si.get("portrait"):
                            portrait = si["portrait"]
                            break
                    if not portrait:
                        portrait = inf.get("portrait") or ""
                    if not portrait and inf.get("style_infos"):
                        portrait = inf["style_infos"][0].get("portrait", "")
                    if portrait:
                        is_url = fmt == "url" and (
                            portrait.startswith("http://") or portrait.startswith("https://")
                        )
                        break
                except Exception:
                    continue
            if portrait:
                cw = self._right_canvas.winfo_width() or 300
                ch = self._right_canvas.winfo_height() or 400
                photo = _load_background_portrait(portrait, is_url, (cw, ch))
            else:
                photo = None
            self.after(0, lambda: self._set_background(photo))

        threading.Thread(target=_fetch, daemon=True).start()

    def _set_background(self, photo: Optional[tk.PhotoImage]) -> None:
        """设置右侧全身照背景（居中显示）"""
        self._bg_photo = photo
        self._right_canvas.delete("bg")
        if photo:
            cw = self._right_canvas.winfo_width() or 400
            ch = self._right_canvas.winfo_height() or 450
            self._right_canvas.create_image(cw // 2, ch // 2, anchor=tk.CENTER, image=photo, tags="bg")
            self._right_canvas.lower("bg")

    def _on_preview_error(self, msg: str, tb: str = "") -> None:
        self._status.config(text="")
        try:
            from gui import show_error_detail
            show_error_detail(self, "试听失败", msg, tb if tb else None)
        except ImportError:
            messagebox.showerror("试听失败", msg + (f"\n\n{tb}" if tb else ""))

    def _copy_tts_cmd(self) -> None:
        """复制当前选中音色的 TTS 命令到剪贴板"""
        sel = self.tree.selection()
        if not sel or sel[0] not in self.voice_map:
            messagebox.showinfo("复制", "请先选择音色")
            return
        style_id, _, _ = self.voice_map[sel[0]]
        cmd = f"\\tts{{こんにちは}}{{ja}}{{{style_id}}}"
        self.clipboard_clear()
        self.clipboard_append(cmd)
        self._status.config(text=f"已复制: {cmd}")

    def _copy_lyrics_cmd(self) -> None:
        """复制当前选中音色的 歌词命令到剪贴板"""
        sel = self.tree.selection()
        if not sel or sel[0] not in self.voice_map:
            messagebox.showinfo("复制", "请先选择音色")
            return
        style_id, _, _ = self.voice_map[sel[0]]
        # \lyrics{字/字}{part_index}{voice_id}{melody}  melody: 0=第一音旋律 1=第二音旋律
        cmd = f"\\lyrics{{字/字}}{{0}}{{{style_id}}}{{0}}"
        self.clipboard_clear()
        self.clipboard_append(cmd)
        self._status.config(text=f"已复制: {cmd}")

    def _no_lyrics_message(self) -> str:
        """找不到 lyrics 时的提示文案"""
        current = self.get_current_file_callback() if self.get_current_file_callback else None
        file_hint = f"当前文件「{current.name}」" if current else "当前内容"
        return (
            f"{file_hint}中没有带 \\lyrics 的简谱。\n\n"
            "• 可在左侧工作区双击切换其他文件（如 VOCALOID.choir、自动和声.choir）\n"
            "• 或在当前文件中添加 \\lyrics{字/字}{0}{音色id}{0}"
        )

    def _on_acappella(self) -> None:
        """清唱生成：用当前选中音色合成简谱歌声（无伴奏），直接播放"""
        if not self.get_score_callback:
            return
        score_text = self.get_score_callback()
        if not score_text or not score_text.strip():
            messagebox.showwarning("清唱生成", self._no_lyrics_message())
            return
        sel = self.tree.selection()
        voice_id = None
        if sel and sel[0] in self.voice_map:
            voice_id, _, _ = self.voice_map[sel[0]]
        self._status.config(text="正在生成清唱...")
        self.update_idletasks()

        def _do():
            try:
                import traceback
                import soundfile as sf
                from lyrics_synth import synthesize_acappella
                result = synthesize_acappella(
                    score_text, sample_rate=44100, voice_id_override=voice_id, base_url=self.base_url
                )
                if not result:
                    self.after(0, lambda: self._acappella_done(self._no_lyrics_message(), None))
                    return
                audio, _ = result
                buf = io.BytesIO()
                sf.write(buf, audio, 44100, format="WAV")
                wav_bytes = buf.getvalue()
                self._status.config(text="")
                threading.Thread(target=_play_wav_bytes, args=(wav_bytes,), daemon=True).start()
            except Exception as e:
                import traceback
                self.after(0, lambda err=str(e), tb=traceback.format_exc(): self._acappella_done(err, tb))

        threading.Thread(target=_do, daemon=True).start()

    def _acappella_done(self, err: str, tb: str | None = None) -> None:
        """清唱生成失败时显示错误"""
        self._status.config(text="")
        if err:
            try:
                from gui import show_error_detail
                show_error_detail(self, "清唱生成失败", err, tb)
            except ImportError:
                messagebox.showerror("清唱生成失败", err + (f"\n\n{tb}" if tb else ""))


def show_voicevox_dialog(
    parent: tk.Tk,
    base_url: str = VOICEVOX_BASE,
    get_score_callback: Optional[Callable[[], str]] = None,
    get_current_file_callback: Optional[Callable[[], "Path | None"]] = None,
) -> None:
    """显示 VOICEVOX 音色选择对话框。get_score_callback 用于清唱生成时获取当前简谱"""
    dlg = VoiceVoxVoiceDialog(parent, base_url, get_score_callback, get_current_file_callback)
    dlg.wait_window()
