"""frpC 现代前端入口（pywebview，跨平台）。

- 后端逻辑（config / validation / controller / status / api_bridge）全部复用，零重写。
- frontend/{index.html,app.css,app.js} 打包时内联为单段 HTML，单文件 exe 不依赖外部资源。
- 配置写入用户数据目录（platforms.config_path()）；frpc 使用内置二进制（运行时自动释出）。
- WebView 后端按平台选择：Windows=edgechromium(WebView2)，Linux=gtk(WebKit2GTK)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import controller as ctrl
import api_bridge as api
import platforms as pf


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

    html = build_html()
    webview.create_window("frpC · frpc 图形化管理", html=html, js_api=api_obj,
                          width=980, height=700, min_size=(820, 560))

    gui = pf.webview_gui_backend()
    if gui:
        webview.start(gui=gui)
    else:
        webview.start()


if __name__ == "__main__":
    main()
