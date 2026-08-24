"""frpC 现代前端入口（pywebview，跨平台）。

- 后端逻辑（config / validation / controller / api_bridge）全部复用，零重写。
- frontend/{index.html,app.css,app.js} 打包时内联为单段 HTML，单文件 exe 不依赖外部资源。
- 配置写入用户数据目录（platforms.config_path()）；frpc 使用内置二进制（运行时自动释出）。
- WebView 后端按平台选择：Windows=edgechromium(WebView2)，Linux=gtk(WebKit2GTK)。
- 系统托盘（pystray）：激活了开机自启或系统服务时，关闭窗口=最小化到托盘后台运行，
  托盘右键「退出」才真正退出；未激活时关闭=正常退出。
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import controller as ctrl
import api_bridge as api
import platforms as pf
import tray as tray_mod


def _resource_dir() -> Path:
    # PyInstaller 单文件模式：frontend 被解压到 _MEIPASS/frontend
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "frontend"
    return Path(__file__).parent.parent / "frontend"


def build_html() -> str:
    """读取前端三件套并内联为单段 HTML（供 webview.create_window(html=...) 使用）。"""
    d = _resource_dir()
    html = (d / "index.html").read_text(encoding="utf-8")
    css = (d / "app.css").read_text(encoding="utf-8")
    js = (d / "app.js").read_text(encoding="utf-8")
    html = html.replace("<!--APP_CSS-->", f"<style>{css}</style>")
    html = html.replace("<!--APP_JS-->", f"<script>{js}</script>")
    return html


def main() -> None:
    import webview

    config_path = pf.config_path()          # 用户数据目录下的 frpc.toml
    frpc_path = ctrl.resolve_frpc_path()    # 内置 frpc 自动释出到数据目录

    api_obj = api.FrpApi(config_path, frpc_path)

    # 「关闭 = 最小化到托盘」的判断：激活了开机自启或系统服务时
    try:
        minimize_to_tray = ctrl.is_boot_autostart() or \
            ctrl.service_status() in ("running", "stopped")
    except Exception:
        minimize_to_tray = False

    state = {"really_quit": False, "window": None}

    html = build_html()
    window = webview.create_window("frpC · frpc 图形化管理", html=html, js_api=api_obj,
                                   width=980, height=700, min_size=(820, 560))
    state["window"] = window

    # ---- 窗口操作辅助：一律放到子线程，规避 closing 内 hide 死锁与 GTK 线程安全问题 ----
    def _hide() -> None:
        try:
            window.hide()
        except Exception:
            pass

    def _show() -> None:
        try:
            window.show()
        except Exception:
            pass

    def _destroy() -> None:
        try:
            window.destroy()
        except Exception:
            pass

    def _spawn(fn) -> None:
        threading.Thread(target=fn, daemon=True).start()

    # ---- 关闭拦截 ----
    def on_closing() -> bool:
        # 返回 True 允许关闭，False 取消关闭
        if state["really_quit"]:
            return True                       # 托盘「退出」→ 允许真正关闭
        if minimize_to_tray:
            _spawn(_hide)                     # 关闭 → 隐藏到托盘（后台运行）
            return False                      # 取消关闭
        return True                           # 未激活自启/服务 → 正常关闭

    window.events.closing += on_closing

    # ---- 最小化 → 隐藏到托盘 ----
    def on_minimized() -> None:
        _spawn(_hide)

    window.events.minimized += on_minimized

    # ---- 系统托盘 ----
    def _tray_show() -> None:
        _spawn(_show)

    def _tray_quit() -> None:
        state["really_quit"] = True
        try:
            tray.stop()
        except Exception:
            pass
        _spawn(_destroy)

    tray = tray_mod.create_tray(on_show=_tray_show, on_quit=_tray_quit)
    tray_mod.run_tray_detached(tray)

    # --minimized：开机自启/服务场景，启动即隐藏到托盘（"无感启动"）
    minimized = "--minimized" in sys.argv

    def _on_gui_start() -> None:
        if minimized:
            _spawn(_hide)

    gui = pf.webview_gui_backend()
    try:
        if gui:
            webview.start(gui=gui, func=_on_gui_start)
        else:
            webview.start(func=_on_gui_start)
    finally:
        # GUI 主循环退出后，停掉托盘，避免残留图标
        try:
            tray.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
