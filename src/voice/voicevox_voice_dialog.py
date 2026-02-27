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

from src.utils.i18n import _
from src.voice.voicevox_client import (
    fetch_speakers,
    fetch_speaker_info,
    fetch_singers,
    clear_singers_cache,
    synthesize_simple,
    resolve_speakers_style_id,
    get_legal_info_for_speaker,
    get_voicevox_mode_label,
    get_voicevox_connection_hint,
    get_effective_voicevox_mode,
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


def _safe_ui(dlg, func):
    """在对话框仍存在时执行 UI 更新，关闭后忽略（避免 TclError）"""
    def _run():
        try:
            if dlg.winfo_exists():
                func()
        except tk.TclError:
            pass
    try:
        dlg.after(0, _run)
    except tk.TclError:
        pass
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
        messagebox.showerror(_("播放失败"), str(e))


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
        self.title(_("VOICEVOX 音色选择"))
        self.geometry("1050x820")
        self.transient(parent)

        self.speakers_data: list[dict] = []
        self.singers_data: list[dict] = []
        self.voice_map: dict[str, tuple[int, str, str, bool, str | None]] = {}  # item_id -> (style_id, name, uuid, is_available, required_vvm)
        self.selected_style_id: Optional[int] = None
        self.selected_speaker_name: Optional[str] = None
        self._icon_photos: dict[str, tk.PhotoImage] = {}  # 列表图标引用
        self._bg_photo: Optional[tk.PhotoImage] = None  # 右侧背景
        self._right_canvas: Optional[tk.Canvas] = None

        main = ttk.Frame(self, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        self._mode_label = ttk.Label(main, text="", font=("", 9), foreground="gray")
        self._mode_label.pack(anchor=tk.W)
        ttk.Label(main, text=_("左侧点击音色即试听（TTS 模式为对话试听，歌唱模式用于歌词合成）")).pack(anchor=tk.W)

        paned = ttk.PanedWindow(main, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=5)

        # 左侧：TTS / 歌唱 两个 tab
        left_frame = ttk.LabelFrame(paned, text=_("音色"), padding=5)
        paned.add(left_frame, weight=1)
        self._notebook = ttk.Notebook(left_frame)
        self._notebook.pack(fill=tk.BOTH, expand=True)
        _style = ttk.Style()
        _style.configure("Treeview", rowheight=44)

        def _make_tree(parent: ttk.Frame, mode: str) -> ttk.Treeview:
            f = ttk.Frame(parent)
            f.pack(fill=tk.BOTH, expand=True)
            tree = ttk.Treeview(f, height=12, show="tree headings", columns=("name", "extra"), selectmode="browse")
            tree.heading("#0", text="")
            tree.heading("name", text=_("角色 - 风格"))
            tree.heading("extra", text="")
            tree.column("#0", width=50, minwidth=50)
            tree.column("name", width=160, minwidth=100)
            tree.column("extra", width=44, minwidth=44)
            scroll = ttk.Scrollbar(f, orient=tk.VERTICAL, command=tree.yview)
            tree.configure(yscrollcommand=scroll.set)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scroll.pack(side=tk.RIGHT, fill=tk.Y)
            tree.bind("<<TreeviewSelect>>", lambda e, m=mode: self._on_select(mode=m))
            return tree

        self._frame_tts = ttk.Frame(self._notebook)
        self._frame_sing = ttk.Frame(self._notebook)
        self._notebook.add(self._frame_tts, text=_("TTS 模式"))
        self._notebook.add(self._frame_sing, text=_("歌唱模式"))
        self.tree_tts = _make_tree(self._frame_tts, "tts")
        self.tree_sing = _make_tree(self._frame_sing, "sing")
        self.tree = self.tree_tts  # 兼容复制命令等使用

        # 右侧：全身照背景 + 利用規約，右上角显示当前音色 ID
        right_frame = ttk.LabelFrame(paned, text=_("角色信息・利用規約"), padding=5)
        paned.add(right_frame, weight=3)
        self._id_label = ttk.Label(right_frame, text=_("ID: —"), font=("", 11))
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
        ttk.Button(btn_frame, text=_("复制 TTS 命令"), command=self._copy_tts_cmd).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text=_("复制 歌词命令"), command=self._copy_lyrics_cmd).pack(side=tk.LEFT, padx=(0, 5))
        if get_score_callback:
            ttk.Button(btn_frame, text=_("清唱生成"), command=self._on_acappella).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text=_("刷新列表"), command=self._load_speakers).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text=_("关闭"), command=self.destroy).pack(side=tk.RIGHT)

        self._status = ttk.Label(main, text="")
        self._status.pack(anchor=tk.W, pady=(5, 0))

        self._load_speakers()

    def _load_speakers(self) -> None:
        """从 API 加载音色列表"""
        self._mode_label.config(text=_("当前模式：{mode}").format(mode=_(get_voicevox_mode_label())))
        self._status.config(text=_("正在连接 VOICEVOX..."))
        self.update_idletasks()
        clear_singers_cache()

        def _fetch():
            try:
                speakers_data = fetch_speakers(self.base_url)
                singers_data = fetch_singers(self.base_url)
                _safe_ui(self, lambda: self._apply_speakers(speakers_data, singers_data))
            except Exception as e:
                import traceback
                err_msg, tb_str = str(e), traceback.format_exc()
                _safe_ui(self, lambda: self._on_error(err_msg, tb_str))

        threading.Thread(target=_fetch, daemon=True).start()

    def _apply_speakers(self, data: list[dict], singers: list[dict] | None = None) -> None:
        from src.voice.voicevox_speaker_catalog import is_speaker_available, get_required_vvm_for_speaker

        self.speakers_data = data
        self.singers_data = singers or []
        self.voice_map = {}  # (mode, item_id) -> (style_id, name, uuid, avail, req_vvm)
        self._icon_photos.clear()
        self.tree_tts.delete(*self.tree_tts.get_children())
        self.tree_sing.delete(*self.tree_sing.get_children())
        is_core = get_effective_voicevox_mode() == "core"
        self.tree_tts.tag_configure("unavailable", foreground="gray")
        self.tree_sing.tag_configure("unavailable", foreground="gray")
        total = 0

        def _add_row(tree: ttk.Treeview, mode: str, sp: dict, st: dict, for_talk: bool, sing_col: str) -> None:
            nonlocal total
            name = sp.get("name", "?")
            uuid = sp.get("speaker_uuid", "") or sp.get("uuid", "")
            sid = st.get("id", 0)
            if sid is None:
                return
            sname = st.get("name", "通常")
            label = f"{name} - {sname}"
            avail = not is_core or is_speaker_available(uuid, for_talk=for_talk)
            req_vvm = get_required_vvm_for_speaker(uuid, for_talk=for_talk) if is_core and not avail else None
            item = tree.insert("", tk.END, text="", values=(label, sing_col), tags=("unavailable",) if not avail else ())
            self.voice_map[(mode, item)] = (sid, name, uuid, avail, req_vvm)
            total += 1

        # TTS 模式：对话用音色（talk 类型）
        for sp in data:
            for st in sp.get("styles", []):
                if st.get("type") in ("frame_decode", "sing"):
                    continue
                _add_row(self.tree_tts, "tts", sp, st, True, "")
        # 歌唱模式：歌唱用音色（frame_decode/sing）
        if self.singers_data:
            for sp in self.singers_data:
                for st in sp.get("styles", []):
                    _add_row(self.tree_sing, "sing", sp, st, False, "是")
        else:
            for sp in data:
                for st in sp.get("styles", []):
                    if st.get("type") not in ("frame_decode", "sing"):
                        continue
                    _add_row(self.tree_sing, "sing", sp, st, False, "是")
        label = get_voicevox_mode_label()
        mode = label.split("（")[0].split(" (")[0].strip()  # "本地库" or "Docker"
        mode_suffix = _("（{mode}）").format(mode=_(mode))
        status_text = _("已加载 {total} 个音色 {mode_suffix}").format(total=total, mode_suffix=mode_suffix)
        if not self.singers_data:
            status_text += _("（未检测到歌唱角色，请安装 s0.vvm 等歌唱音声库）")
        status_text += _("，正在加载头像...")
        self._status.config(text=status_text)
        last_style = _load_last_style_id()
        threading.Thread(target=self._load_all_icons, daemon=True).start()
        # 恢复上次选择的音色（仅展示，不自动试听）
        if last_style is not None:
            for (m, iid), (sid, _n, _u, _a, _r) in self.voice_map.items():
                if sid == last_style:
                    t = self.tree_tts if m == "tts" else self.tree_sing
                    def _restore(item=iid, tr=t, mode=m):
                        try:
                            if self.winfo_exists():
                                tr.selection_set(item)
                                tr.see(item)
                                self._on_select(skip_preview=True, mode=mode)
                        except tk.TclError:
                            pass
                    self.after(200, _restore)
                    return

    def _load_all_icons(self) -> None:
        """后台为每项加载 icon（TTS 与歌唱模式均显示头像）"""
        info_cache: dict[str, tuple[list, str, bool, Optional[tk.PhotoImage]]] = {}  # uuid -> (style_infos, portrait, is_url, fallback_icon)
        # 优先 speakers_data（含完整 styles），再 singers_data；同 uuid 复用缓存，确保歌唱模式也显示
        for sp in self.speakers_data + self.singers_data:
            uuid = sp.get("speaker_uuid", "") or sp.get("uuid", "")
            if not uuid:
                continue
            try:
                if uuid not in info_cache:
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
                    is_url = fmt == "url" and info.get("_format") != "base64"
                    fallback_icon = None
                    for si in style_infos:
                        if si.get("icon"):
                            fallback_icon = _load_list_icon(
                                si["icon"],
                                is_url=is_url and (si["icon"].startswith("http://") or si["icon"].startswith("https://")),
                            )
                            break
                    if not fallback_icon and portrait:
                        fallback_icon = _load_list_icon(
                            portrait,
                            is_url=is_url and (portrait.startswith("http://") or portrait.startswith("https://")),
                        )
                    info_cache[uuid] = (style_infos, portrait, is_url, fallback_icon)
                style_infos, portrait, is_url, fallback_icon = info_cache[uuid]
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
                    if not icon:
                        icon = fallback_icon
                    if icon:
                        for (m, iid), (isid, _n, cuuid, _a, _r) in self.voice_map.items():
                            if cuuid == uuid and isid == sid:
                                self._icon_photos[(m, iid)] = icon
                                tr = self.tree_tts if m == "tts" else self.tree_sing
                                _safe_ui(self, lambda x=iid, img=icon, t=tr: t.item(x, image=img))
            except Exception:
                pass
        _safe_ui(self, lambda: self._status.config(text=_("已加载 {total} 个音色").format(total=len(self.voice_map))))

    def _on_error(self, msg: str, tb: str = "") -> None:
        self._status.config(text="")
        try:
            from src.ui.gui import show_error_detail
            show_error_detail(self, _("VOICEVOX 连接失败"), msg, tb if tb else None)
        except ImportError:
            messagebox.showerror(_("VOICEVOX 连接失败"), msg + (f"\n\n{tb}" if tb else ""))
        self._status.config(text=_("连接失败：") + _(get_voicevox_connection_hint()))

    def _on_select(self, event=None, skip_preview: bool = False, mode: str = "tts") -> None:
        tree = self.tree_tts if mode == "tts" else self.tree_sing
        sel = tree.selection()
        if not sel:
            return
        item_id = sel[0]
        key = (mode, item_id)
        if key not in self.voice_map:
            self._id_label.config(text=_("ID: —"))
            self._set_background(None)
            return
        style_id, speaker_name, speaker_uuid, is_available, required_vvm = self.voice_map[key]
        self.selected_style_id = style_id
        self.selected_speaker_name = speaker_name
        self._id_label.config(text=f"ID: {style_id}")
        _save_last_style_id(style_id)
        info = get_legal_info_for_speaker(speaker_name)
        self.legal_text.config(state=tk.NORMAL)
        self.legal_text.delete(1.0, tk.END)
        self.legal_text.insert(tk.END, info)
        self.legal_text.config(state=tk.DISABLED)
        # 未下载角色：点击时提示需下载的 VVM
        if not is_available and required_vvm and not skip_preview:
            self._status.config(text="")
            if messagebox.askyesno(_("需要下载音声模型"), _("使用此音色需要下载 {vvm}。\n是否立即下载？").format(vvm=required_vvm)):
                from src.voice.voicevox_model_dialog import show_vvm_download_dialog
                ok, err = show_vvm_download_dialog(self.winfo_toplevel(), required_vvm)
                if ok:
                    messagebox.showinfo(_("下载完成"), _("{vvm} 已下载，请点击「刷新列表」后重试。").format(vvm=required_vvm))
                elif err:
                    messagebox.showerror(_("下载失败"), err)
            return
        # 点击即试听（TTS 模式试听对话，歌唱模式仅展示）
        if skip_preview:
            self._status.config(text="")
        elif mode == "tts":
            self._status.config(text=_("正在合成试听..."))
        else:
            self._status.config(text=_("歌唱模式：用于歌词合成，点击「清唱生成」试听"))
        self.update_idletasks()

        if not skip_preview and mode == "tts":
            def _synth():
                try:
                    from src.voice.voicevox_client import get_effective_voicevox_mode
                    preview_id = style_id
                    if get_effective_voicevox_mode() == "core":
                        from src.voice.voicevox_core_backend import load_vvm_for_speaker, resolve_style_id_for_speaker
                        if load_vvm_for_speaker(speaker_uuid, for_talk=True):
                            resolved = resolve_style_id_for_speaker(speaker_uuid, for_talk=True)
                            if resolved is not None:
                                preview_id = resolved
                    elif self.singers_data:
                        resolved = resolve_speakers_style_id(style_id, self.base_url)
                        if resolved is not None:
                            preview_id = resolved
                    wav = synthesize_simple(PREVIEW_TEXT, preview_id, self.base_url)
                    threading.Thread(target=_play_wav_bytes, args=(wav,), daemon=True).start()
                    _safe_ui(self, lambda: self._status.config(text=""))
                except Exception as e:
                    err_msg, tb_str = str(e), __import__("traceback").format_exc()
                    if "StyleNotFound" in type(e).__name__ or "スタイルが見つかりません" in str(e):
                        from src.voice.voicevox_speaker_catalog import get_required_vvm_for_speaker
                        from src.voice.voicevox_model_dialog import show_vvm_download_dialog
                        vvm = get_required_vvm_for_speaker(speaker_uuid, for_talk=True)
                        if vvm:
                            def _do_dl():
                                ok, err = show_vvm_download_dialog(self.winfo_toplevel(), vvm)
                                _safe_ui(self, lambda: self._status.config(text=""))
                                if ok:
                                    _safe_ui(self, self._load_speakers)
                            _safe_ui(self, _do_dl)
                            return
                    _safe_ui(self, lambda: self._on_preview_error(err_msg, tb_str))

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
            _safe_ui(self, lambda: self._set_background(photo))

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
            from src.ui.gui import show_error_detail
            show_error_detail(self, _("试听失败"), msg, tb if tb else None)
        except ImportError:
            messagebox.showerror(_("试听失败"), msg + (f"\n\n{tb}" if tb else ""))

    def _get_selected_key(self) -> tuple[str, str] | None:
        """获取当前选中项 (mode, item_id)，优先 TTS tab"""
        for tree, mode in [(self.tree_tts, "tts"), (self.tree_sing, "sing")]:
            sel = tree.selection()
            if sel and (mode, sel[0]) in self.voice_map:
                return (mode, sel[0])
        return None

    def _copy_tts_cmd(self) -> None:
        """复制当前选中音色的 TTS 命令到剪贴板"""
        key = self._get_selected_key()
        if not key or key not in self.voice_map:
            messagebox.showinfo(_("复制"), _("请先选择音色（TTS 模式下选对话音色）"))
            return
        style_id, _n, _u, _a, _r = self.voice_map[key]
        cmd = f"\\tts{{こんにちは}}{{ja}}{{{style_id}}}"
        self.clipboard_clear()
        self.clipboard_append(cmd)
        self._status.config(text=f"已复制: {cmd}")

    def _copy_lyrics_cmd(self) -> None:
        """复制当前选中音色的 歌词命令到剪贴板"""
        key = self._get_selected_key()
        if not key or key not in self.voice_map:
            messagebox.showinfo(_("复制"), _("请先选择音色（歌唱模式下选歌唱音色）"))
            return
        style_id, _n, _u, _a, _r = self.voice_map[key]
        # \lyrics{字/字}{part_index}{voice_id}{melody}  melody: 0=第一音旋律 1=第二音旋律
        cmd = f"\\lyrics{{字/字}}{{0}}{{{style_id}}}{{0}}"
        self.clipboard_clear()
        self.clipboard_append(cmd)
        self._status.config(text=_("已复制: {cmd}").format(cmd=cmd))

    def _no_lyrics_message(self) -> str:
        """找不到 lyrics 时的提示文案"""
        current = self.get_current_file_callback() if self.get_current_file_callback else None
        file_hint = _("当前文件「{name}」").format(name=current.name) if current else _("当前内容")
        return (
            _("{hint}中没有带 \\lyrics 的简谱。\n\n• 可在左侧工作区双击切换其他文件（如 VOCALOID.choir、自动和声.choir）\n• 或在当前文件中添加 \\lyrics{{字/字}}{{0}}{{音色id}}{{0}}").format(hint=file_hint)
        )

    def _on_acappella(self) -> None:
        """清唱生成：用当前选中音色合成简谱歌声（无伴奏），直接播放。乐谱中所有 \\lyrics 的 voice_id 均替换为选中角色。"""
        if not self.get_score_callback:
            return
        score_text = self.get_score_callback()
        if not score_text or not score_text.strip():
            messagebox.showwarning(_("清唱生成"), self._no_lyrics_message())
            return
        sel = self.tree.selection()
        voice_id = None
        key = self._get_selected_key()
        if key and key in self.voice_map:
            sid, _n, uuid, _a, _r = self.voice_map[key]
            if key[0] == "sing":
                voice_id = sid
            else:
                # TTS tab 选中：尝试找同角色的歌唱 style_id
                for (m, _i), (s, _n, u, _a, _r) in self.voice_map.items():
                    if m == "sing" and u == uuid:
                        voice_id = s
                        break
                if voice_id is None:
                    voice_id = sid
        if voice_id is None:
            messagebox.showwarning(_("清唱生成"), _("请先在左侧音色列表中点击选择要使用的角色。"))
            return
        self._status.config(text=_("正在生成清唱..."))
        self.update_idletasks()

        def _do():
            try:
                import traceback
                import soundfile as sf
                from src.voice.lyrics_synth import synthesize_acappella
                result = synthesize_acappella(
                    score_text, sample_rate=44100, voice_id_override=voice_id, base_url=self.base_url
                )
                if not result:
                    _safe_ui(self, lambda: self._acappella_done(self._no_lyrics_message(), None))
                    return
                audio, _sr = result
                buf = io.BytesIO()
                sf.write(buf, audio, 44100, format="WAV")
                wav_bytes = buf.getvalue()
                self._status.config(text="")
                threading.Thread(target=_play_wav_bytes, args=(wav_bytes,), daemon=True).start()
            except Exception as e:
                err_str, err_type = str(e), type(e).__name__
                from src.voice.voicevox_model_dialog import should_show_vvm_download_for_error, show_style_not_found_dialog
                if should_show_vvm_download_for_error(err_str, err_type):
                    req_vvm = None
                    if sel and sel[0] in self.voice_map:
                        _s, _n, uuid, _a, _r = self.voice_map[sel[0]]
                        from src.voice.voicevox_speaker_catalog import get_required_vvm_for_speaker
                        req_vvm = get_required_vvm_for_speaker(uuid, for_talk=False)
                    def _show_dl():
                        if show_style_not_found_dialog(self.winfo_toplevel(), for_sing=True, specific_vvm=req_vvm, err_msg=err_str):
                            self._load_speakers()
                    _safe_ui(self, _show_dl)
                    return
                import traceback
                _safe_ui(self, lambda err=err_str, tb=traceback.format_exc(): self._acappella_done(err, tb))

        threading.Thread(target=_do, daemon=True).start()

    def _acappella_done(self, err: str, tb: str | None = None) -> None:
        """清唱生成失败时显示错误"""
        self._status.config(text="")
        if err:
            try:
                from src.ui.gui import show_error_detail
                show_error_detail(self, _("清唱生成失败"), err, tb)
            except ImportError:
                messagebox.showerror(_("清唱生成失败"), err + (f"\n\n{tb}" if tb else ""))


def show_voicevox_dialog(
    parent: tk.Tk,
    base_url: str = VOICEVOX_BASE,
    get_score_callback: Optional[Callable[[], str]] = None,
    get_current_file_callback: Optional[Callable[[], "Path | None"]] = None,
) -> None:
    """显示 VOICEVOX 音色选择对话框。get_score_callback 用于清唱生成时获取当前简谱"""
    dlg = VoiceVoxVoiceDialog(parent, base_url, get_score_callback, get_current_file_callback)
    dlg.wait_window()
