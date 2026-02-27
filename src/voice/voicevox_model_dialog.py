"""
VOICEVOX 音声模型管理对话框
选择并下载 VVM 模型，安装 voicevox_core 依赖。
"""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
from pathlib import Path
from typing import Callable, Optional

from src.utils.i18n import _
from src.voice.voicevox_core_install import install_voicevox_core, is_voicevox_core_installed, get_wheel_url
from src.voice.voicevox_model_catalog import VVM_CATALOG
from src.voice.voicevox_model_manager import (
    download_vvm,
    delete_vvm,
    run_download_script,
    is_vvm_installed,
    is_core_ready,
    get_vvm_dir,
    get_open_jtalk_dict_dir,
)


def _safe_ui(dlg, func):
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


def show_voicevox_model_dialog(parent: tk.Tk) -> None:
    """显示音声模型管理对话框"""
    dlg = tk.Toplevel(parent)
    dlg.title(_("VOICEVOX 音声模型管理"))
    dlg.geometry("620x520")
    dlg.transient(parent)

    main = ttk.Frame(dlg, padding=15)
    main.pack(fill=tk.BOTH, expand=True)

    # 状态区
    status_frame = ttk.LabelFrame(main, text=_("状态"), padding=8)
    status_frame.pack(fill=tk.X, pady=(0, 10))
    status_text = scrolledtext.ScrolledText(status_frame, height=6, font=("", 10), wrap=tk.WORD, state=tk.DISABLED, bg="#f8f8f8")
    status_text.pack(fill=tk.X)

    def _status(msg: str) -> None:
        def _do():
            status_text.config(state=tk.NORMAL)
            status_text.insert(tk.END, msg + "\n")
            status_text.see(tk.END)
            status_text.config(state=tk.DISABLED)
        _safe_ui(dlg, _do)

    def _refresh_status() -> None:
        lines = []
        try:
            from src.utils.voicevox_settings import get_voicevox_backend
            if get_voicevox_backend() == "docker":
                lines.append(_("提示：当前设置为 Docker 模式，音色来自 voicevox_engine 容器。此功能用于本地库模式。"))
        except Exception:
            pass
        if is_voicevox_core_installed():
            lines.append(_("✓ voicevox_core 已安装"))
        else:
            url = get_wheel_url()
            lines.append(_("✗ voicevox_core 未安装") + (_("（可安装，平台支持）") if url else _("（当前平台暂无预编译包）")))

        dict_dir = get_open_jtalk_dict_dir()
        if dict_dir.exists():
            lines.append(_("✓ Open JTalk 辞書已就绪"))
        else:
            lines.append(_("✗ Open JTalk 辞書未安装（需运行「安装依赖」）"))

        installed = [e.filename for e in VVM_CATALOG if is_vvm_installed(e.filename)]
        lines.append(_("已下载模型: {list}").format(list=', '.join(installed) if installed else _("无")))
        if is_core_ready():
            lines.append(_("\n✓ 本地模式可用，无需 Docker"))
        else:
            lines.append(_("\n✗ 请完成上述安装后使用本地模式，或使用 Docker 版 voicevox_engine"))
        _status("\n".join(lines))

    # 操作按钮
    btn_frame = ttk.Frame(main)
    btn_frame.pack(fill=tk.X, pady=(0, 8))
    ttk.Button(btn_frame, text=_("安装 voicevox_core"), command=lambda: _do_install(_status)).pack(side=tk.LEFT, padx=(0, 5))
    ttk.Button(btn_frame, text=_("安装 ONNX/辞書"), command=lambda: _do_deps(_status)).pack(side=tk.LEFT, padx=(0, 5))
    ttk.Button(btn_frame, text=_("刷新状态"), command=_refresh_status).pack(side=tk.LEFT, padx=(0, 5))

    # 模型列表
    list_frame = ttk.LabelFrame(main, text=_("可选音声模型（选择后点击下载/删除，Ctrl+点击多选）"), padding=8)
    list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

    tree = ttk.Treeview(list_frame, height=12, columns=("desc", "size", "installed"), show="headings", selectmode="extended")
    tree.heading("desc", text=_("说明"))
    tree.heading("size", text=_("大小"))
    tree.heading("installed", text=_("已下载"))
    tree.column("desc", width=380)
    tree.column("size", width=60)
    tree.column("installed", width=60)
    scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)

    for e in VVM_CATALOG:
        inst = "✓" if is_vvm_installed(e.filename) else ""
        tree.insert("", tk.END, iid=e.filename, values=(e.desc, f"~{e.size_mb}MB", inst), tags=(e.filename,))
    tree.tag_configure("s0.vvm", foreground="#0066cc")

    def _do_download() -> None:
        selected = list(tree.selection())
        if not selected:
            messagebox.showinfo(_("提示"), _("请先选择要下载的模型（Ctrl+点击可多选）"))
            return
        def _run():
            for f in selected:
                ok, err = download_vvm(f, status_callback=_status)
                if not ok:
                    _status(_("下载 {f} 失败: {err}").format(f=f, err=err))
                else:
                    _status(_("✓ {f} 下载完成").format(f=f))
                    _safe_ui(dlg, lambda fn=f: tree.set(fn, "installed", "✓"))
            _safe_ui(dlg, _refresh_status)
        threading.Thread(target=_run, daemon=True).start()

    ttk.Button(btn_frame, text=_("下载选中模型"), command=_do_download).pack(side=tk.LEFT, padx=(20, 0))

    def _do_delete() -> None:
        selected = list(tree.selection())
        if not selected:
            messagebox.showinfo(_("提示"), _("请先选择要删除的模型（仅可删除已下载的，Ctrl+点击可多选）"))
            return
        to_delete = [f for f in selected if is_vvm_installed(f)]
        if not to_delete:
            messagebox.showinfo(_("提示"), _("所选模型未下载，无需删除"))
            return
        names = "、".join(to_delete)
        if not messagebox.askyesno(_("确认删除"), _("确定要删除以下模型吗？\n{names}\n\n删除后可重新下载。").format(names=names)):
            return
        for f in to_delete:
            ok, err = delete_vvm(f)
            if not ok:
                messagebox.showerror(_("删除失败"), f"{f}: {err}")
            else:
                _status(_("✓ 已删除 {f}").format(f=f))
                tree.set(f, "installed", "")
        _refresh_status()

    ttk.Button(btn_frame, text=_("删除选中模型"), command=_do_delete).pack(side=tk.LEFT, padx=(5, 0))

    def _do_install(cb) -> None:
        def _run():
            ok, err = install_voicevox_core(status_callback=cb)
            if ok:
                cb(_("✓ voicevox_core 安装成功"))
            else:
                cb(_("✗ 安装失败: {err}").format(err=err))
            _safe_ui(dlg, _refresh_status)
        threading.Thread(target=_run, daemon=True).start()

    def _do_deps(cb) -> None:
        def _run():
            cb(_("正在下载并运行 voicevox_core 官方安装脚本..."))
            ok, err = run_download_script(status_callback=cb)
            if ok:
                cb(_("✓ ONNX Runtime 与 Open JTalk 辞書安装完成"))
            else:
                cb(_("✗ 安装失败: {err}").format(err=err))
            _safe_ui(dlg, _refresh_status)
        threading.Thread(target=_run, daemon=True).start()

    ttk.Button(main, text=_("关闭"), command=dlg.destroy).pack(anchor=tk.E)
    _refresh_status()


def show_vvm_download_dialog(parent: tk.Tk, filename: str) -> tuple[bool, str]:
    """
    弹出单文件 VVM 下载对话框，带进度条。
    返回 (成功, 错误信息)
    """
    dlg = tk.Toplevel(parent)
    dlg.title(_("下载 {filename}").format(filename=filename))
    dlg.geometry("420x140")
    dlg.transient(parent)
    dlg.grab_set()

    main = ttk.Frame(dlg, padding=15)
    main.pack(fill=tk.BOTH, expand=True)

    ttk.Label(main, text=_("正在下载 {filename}...").format(filename=filename)).pack(anchor=tk.W)
    progress = ttk.Progressbar(main, mode="determinate", length=380)
    progress.pack(fill=tk.X, pady=8)
    status = ttk.Label(main, text="")
    status.pack(anchor=tk.W)

    result: list[tuple[bool, str]] = []

    def _status(msg: str) -> None:
        def _do():
            try:
                if dlg.winfo_exists():
                    status.config(text=msg)
                    dlg.update_idletasks()
            except tk.TclError:
                pass
        try:
            dlg.after(0, _do)
        except tk.TclError:
            pass

    def _progress(pct: int) -> None:
        def _do():
            try:
                if dlg.winfo_exists():
                    progress["value"] = pct
                    dlg.update_idletasks()
            except tk.TclError:
                pass
        try:
            dlg.after(0, _do)
        except tk.TclError:
            pass

    def _run() -> None:
        def cb(msg: str) -> None:
            _status(msg)
            if "%" in msg:
                import re
                m = re.search(r"(\d+)\s*%", msg)
                if m:
                    try:
                        pct = int(m.group(1))
                        if 0 <= pct <= 100:
                            _progress(pct)
                    except (ValueError, IndexError):
                        pass
        ok, err = download_vvm(filename, status_callback=cb)
        result.append((ok, err))
        try:
            dlg.after(0, lambda: dlg.destroy())
        except tk.TclError:
            pass

    threading.Thread(target=_run, daemon=True).start()
    dlg.wait_window()
    return result[0] if result else (False, _("未完成"))


def _infer_vvm_from_synthesis_error(err_msg: str) -> str | None:
    """
    从合成错误信息推断需要的 VVM。
    仅当明确为模型/风格缺失时返回；乐谱错误、网络错误等不匹配。
    TTS 失败（对话模型缺失）-> 0.vvm；歌唱失败（歌词模型缺失）-> s0.vvm。
    """
    if not err_msg:
        return None
    # 排除非模型缺失类错误：乐谱无效、网络、连接等
    excl = ("不正な", "invalid", "500", "Connection", "连接", "mora kana")
    if any(x in err_msg for x in excl):
        return None
    # TTS/对话模型缺失：明确提及需下载对话模型
    if "对话" in err_msg and ("0.vvm" in err_msg or "下载" in err_msg or "模型" in err_msg):
        return "0.vvm"
    if "0.vvm" in err_msg and ("下载" in err_msg or "需要" in err_msg):
        return "0.vvm"
    # 歌唱/歌词模型缺失：风格未找到、未找到歌唱角色、明确提及 s0.vvm
    if "スタイルが見つかりません" in err_msg or "未找到歌唱" in err_msg:
        return "s0.vvm"
    if "s0.vvm" in err_msg and ("下载" in err_msg or "安装" in err_msg or "需要" in err_msg):
        return "s0.vvm"
    return None


def should_show_vvm_download_for_error(err_msg: str, err_type: str = "") -> bool:
    """
    判断合成错误是否应弹出 VVM 下载提示。
    仅当明确为 TTS 或歌词模型缺失时返回 True，其他错误（乐谱无效、网络等）不提示下载。
    """
    if not err_msg and not err_type:
        return False
    # 异常类型明确为 StyleNotFound
    if "StyleNotFound" in err_type:
        return True
    # 错误信息明确为风格/模型未找到
    if "スタイルが見つかりません" in (err_msg or ""):
        return True
    return _infer_vvm_from_synthesis_error(err_msg or "") is not None


def show_style_not_found_dialog(
    parent: tk.Tk,
    for_sing: bool = False,
    specific_vvm: str | None = None,
    err_msg: str | None = None,
) -> bool:
    """
    当 TTS 或歌唱合成失败时弹出下载提示。
    specific_vvm 有值时优先使用；否则从 err_msg 推断；否则 for_sing=True 建议 s0.vvm，否则 0.vvm。
    返回 True 表示用户已下载并可能重试。
    """
    vvm = specific_vvm or (_infer_vvm_from_synthesis_error(err_msg or "")) or ("s0.vvm" if for_sing else "0.vvm")
    if not messagebox.askyesno(_("需要下载音声模型"), _("使用此音色需要下载 {vvm}。\n是否立即下载？").format(vvm=vvm)):
        return False
    ok, err = show_vvm_download_dialog(parent, vvm)
    if ok:
        messagebox.showinfo(_("下载完成"), _("{vvm} 已下载，请重试。").format(vvm=vvm))
        return True
    if err:
        messagebox.showerror(_("下载失败"), err)
    return False
