"""系统托盘图标（pystray + Pillow）。

- 用 Pillow 在运行时生成图标，无需打包外部图片资源。
- 托盘菜单：显示主界面 / 退出。
- 托盘主循环跑在 daemon 线程（pystray 官方 FAQ 推荐的多线程集成方式），
  主线程继续跑 pywebview 的 GUI 主循环，二者互不阻塞。
"""
from __future__ import annotations

import threading
from typing import Callable

import pystray
from PIL import Image, ImageDraw

TRAY_NAME = "frpC"


def create_icon_image(size: int = 64) -> Image.Image:
    """运行时生成一个简洁的蓝色圆角方块图标（中心一个浅色圆点表示连接）。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, size - 4, size - 4), radius=14,
                        fill=(59, 120, 255, 255))
    r = size // 6
    d.ellipse((size // 2 - r, size // 2 - r, size // 2 + r, size // 2 + r),
              fill=(255, 255, 255, 255))
    return img


def create_tray(on_show: Callable[[], None], on_quit: Callable[[], None]) -> pystray.Icon:
    """创建托盘图标。

    on_show / on_quit 在托盘线程被调用；调用方负责线程安全（操作 GUI 窗口时）。
    """
    menu = pystray.Menu(
        pystray.MenuItem("显示主界面", lambda icon, item: on_show()),
        pystray.MenuItem("退出", lambda icon, item: on_quit()),
    )
    return pystray.Icon(TRAY_NAME, create_icon_image(),
                        "frpC · frpc 图形化管理", menu=menu)


def run_tray_detached(tray: pystray.Icon) -> None:
    """在 daemon 线程运行托盘主循环，不阻塞主线程。"""
    threading.Thread(target=tray.run, name="frpC-tray", daemon=True).start()
