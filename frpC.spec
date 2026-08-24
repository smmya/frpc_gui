# -*- mode: python ; coding: utf-8 -*-
# frpC 打包配置（跨平台：Windows / Linux）
#
# - 内置 frpc 二进制（vendor/frpc/frpc(.exe)）与版本标记一并打入单文件，
#   运行时由 platforms.extract_frpc() 释出到用户数据目录。
# - Windows 使用 edgechromium 后端（WebView2），需 pythonnet/clr_loader 兜底；
#   Linux 使用 gtk 后端（PyGObject + WebKit2GTK），需收集 gi 与 webview.platforms.gtk。
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, collect_all

IS_WIN = sys.platform == "win32"
frpc_bin = "frpc.exe" if IS_WIN else "frpc"

hiddenimports = ["webview"] + collect_submodules("webview")
binaries = [(str(Path("vendor/frpc") / frpc_bin), ".")]
datas = [
    ("frontend", "frontend"),
    (str(Path("vendor/frpc") / "frpc.version"), "."),
]

if IS_WIN:
    hiddenimports += ["pythonnet", "clr_loader"]
    hiddenimports += collect_submodules("pythonnet")
    hiddenimports += collect_submodules("clr_loader")
else:
    # Linux：PyGObject/GTK 后端。collect_all('gi') 一并收集子模块、二进制与 typelib。
    hiddenimports += ["webview.platforms.gtk"]
    gi_datas, gi_bins, gi_hidden = collect_all("gi")
    hiddenimports += gi_hidden
    datas += gi_datas
    binaries += gi_bins

a = Analysis(
    ["src/webview_main.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="frpC",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
