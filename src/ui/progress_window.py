"""
生成进度：中央弹出，可最小化到主窗口状态栏（PyCharm 风格），不阻塞 UI。
"""
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional


class ProgressWindow:
    """进度窗口：支持中央显示、最小化到主窗口状态栏、线程安全更新"""

    def __init__(
        self,
        parent: tk.Tk,
        title: str = "生成进度",
        status_frame: Optional[ttk.Frame] = None,
    ):
        self.parent = parent
        self.title_text = title
        self.status_frame = status_frame  # 主窗口状态栏，最小化时嵌入此处
        self.win: Optional[tk.Toplevel] = None
        self._minimized = False
        self._progress_var = tk.DoubleVar(value=0)
        self._status_var = tk.StringVar(value="准备中...")
        self._progress_bar: Optional[ttk.Progressbar] = None
        self._status_label: Optional[ttk.Label] = None
        self._btn_frame: Optional[ttk.Frame] = None
        self._embedded_frame: Optional[ttk.Frame] = None
        self._on_close: Optional[Callable[[], None]] = None
        self._on_restore: Optional[Callable[[], None]] = None

    def show(self) -> None:
        """显示窗口（中央）"""
        if self.win and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self._minimized = False
            return
        self.win = tk.Toplevel(self.parent)
        self.win.title(self.title_text)
        self.win.transient(self.parent)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self._minimize)

        main = ttk.Frame(self.win, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        self._status_label = ttk.Label(main, textvariable=self._status_var)
        self._status_label.pack(anchor=tk.W)
        self._progress_bar = ttk.Progressbar(main, variable=self._progress_var, maximum=100)
        self._progress_bar.pack(fill=tk.X, pady=(8, 10))

        self._btn_frame = ttk.Frame(main)
        self._btn_frame.pack(fill=tk.X)
        ttk.Button(self._btn_frame, text="最小化到状态栏", command=self._minimize).pack(side=tk.LEFT, padx=(0, 5))

        self._minimized = False
        self._place_center()

    def _place_center(self) -> None:
        """居中放置"""
        if not self.win:
            return
        self.win.update_idletasks()
        w, h = 320, 120
        px = self.parent.winfo_rootx()
        py = self.parent.winfo_rooty()
        pw = self.parent.winfo_width()
        ph = self.parent.winfo_height()
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
        self.win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")

    def _minimize(self) -> None:
        """最小化到主窗口状态栏"""
        if not self.status_frame:
            return
        try:
            if self.win and self.win.winfo_exists():
                self.win.withdraw()
        except tk.TclError:
            pass
        self._minimized = True
        self._show_embedded()

    def _show_embedded(self) -> None:
        """在状态栏显示嵌入的进度条"""
        if not self.status_frame or self._embedded_frame:
            return
        self._embedded_frame = ttk.Frame(self.status_frame)
        self._embedded_frame.pack(side=tk.RIGHT, padx=(0, 15))
        ttk.Label(self._embedded_frame, textvariable=self._status_var, font=("", 9)).pack(side=tk.LEFT, padx=(0, 6))
        bar = ttk.Progressbar(self._embedded_frame, variable=self._progress_var, maximum=100, length=120)
        bar.pack(side=tk.LEFT, padx=(0, 6))
        lbl = ttk.Label(self._embedded_frame, text="点击恢复", font=("", 8), foreground="gray", cursor="hand2")
        lbl.pack(side=tk.LEFT)
        lbl.bind("<Button-1>", lambda e: self._restore())
        self._embedded_frame.bind("<Button-1>", lambda e: self._restore())

    def _hide_embedded(self) -> None:
        """隐藏状态栏中的嵌入进度"""
        if self._embedded_frame and self._embedded_frame.winfo_exists():
            self._embedded_frame.destroy()
            self._embedded_frame = None

    def _restore(self) -> None:
        """从最小化恢复"""
        self._hide_embedded()
        self._minimized = False
        if self.win and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self._place_center()
        else:
            self.show()
        if self._on_restore:
            self._on_restore()

    def update(self, status: str, percent: float) -> None:
        """更新进度（需在主线程调用，如通过 root.after）"""
        self._status_var.set(status)
        self._progress_var.set(min(100, max(0, percent)))

    def close(self) -> None:
        """关闭窗口"""
        self._hide_embedded()
        if self.win and self.win.winfo_exists():
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None
        self._minimized = False
        if self._on_close:
            self._on_close()

    def set_on_close(self, cb: Callable[[], None]) -> None:
        """设置关闭时的回调"""
        self._on_close = cb
