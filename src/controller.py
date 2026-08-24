"""frpc 控制器（平台无关门面）。

对外保持稳定的函数接口（供 api_bridge / 入口 / 测试调用），内部全部委托
platforms.py 的跨平台实现。职责：定位/释出 frpc 二进制、启动/停止进程、
系统服务、开机自启、状态查询。

frpc 二进制解析优先级：
1. 内置 frpc（打包时嵌入）——运行时自动释出到数据目录（最高优先级、最可靠）；
2. 用户显式指定路径；
3. 程序同目录 / PATH（源码开发场景）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import platforms as pf

# 常量透传（向后兼容）
SERVICE_NAME = pf.SERVICE_NAME
RUN_KEY = pf.RUN_KEY
RUN_VALUE = pf.RUN_VALUE


def _own_path() -> str:
    """返回当前程序自身的绝对路径（源码或 PyInstaller 单文件均适用）。"""
    return os.path.abspath(sys.argv[0])


def _is_self(path: str) -> bool:
    """判断某路径是否就是本程序自身（防止把 frpC 当成 frpc 启动）。"""
    try:
        return os.path.samefile(path, _own_path())
    except (OSError, ValueError):
        return Path(path).name.lower() == Path(_own_path()).name.lower()


def _assert_not_self(frpc_path: str) -> None:
    """启动/安装服务前硬校验，避免把 GUI 自身当成 frpc。"""
    if _is_self(frpc_path):
        raise RuntimeError(
            f"错误：指定的 frpc 路径 '{frpc_path}' 似乎是本程序自身。"
            "请使用内置的 frpc，或重新指定正确的 frpc 路径。"
        )


def resolve_frpc_path(preferred: Optional[str] = None) -> Optional[str]:
    """定位 frpc 二进制路径（跨平台）。

    优先释出并使用内置 frpc；无内置时回退到用户指定 / 同目录 / PATH。
    关键安全约束：返回的候选路径不能是本程序自身（frpC），防止 Windows
    大小写不敏感地把 frpC 识别成 frpc 后无限自我复制。
    """
    # 1) 内置 frpc（打包嵌入）——释出到数据目录
    bundled = pf.extract_frpc()
    if bundled and not _is_self(str(bundled)):
        return str(bundled)

    # 2) 回退搜索
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred)
    app_dir = Path(sys.argv[0]).parent
    candidates.append(str(app_dir / pf.frpc_binary_name()))
    path_frpc = shutil.which(pf.frpc_binary_name()) or shutil.which("frpc")
    if path_frpc:
        candidates.append(path_frpc)

    for cand in candidates:
        p = Path(cand)
        if p.exists() and not _is_self(str(p)):
            return str(p)
    return None


def get_frpc_version(frpc_path: str) -> str:
    if _is_self(frpc_path):
        raise RuntimeError("错误：尝试让本程序自身执行 --version")
    proc = subprocess.run(
        [frpc_path, "--version"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        **pf._run_kwargs(),
    )
    out = (proc.stdout or proc.stderr or "").strip()
    if not out and proc.returncode != 0:
        out = "未知"
    return out.splitlines()[0] if out else "未知"


# ---------------- 前台进程 ----------------
def start_process(frpc_path: str, config_path: str | Path) -> int:
    _assert_not_self(frpc_path)
    return pf.start_process(frpc_path, config_path)


def is_pid_alive(pid: int) -> bool:
    return pf.is_pid_alive(pid)


def stop_process(config_path: str | Path) -> None:
    pf.stop_process(config_path)


def process_running(config_path: str | Path) -> bool:
    return pf.process_running(config_path)


# ---------------- 系统服务 ----------------
def service_status() -> str:
    return pf.service_status()


def install_service(frpc_path: str, config_path: str | Path) -> None:
    _assert_not_self(frpc_path)
    pf.install_service(frpc_path, config_path)


def uninstall_service(frpc_path: Optional[str] = None) -> None:
    pf.uninstall_service(frpc_path)


def start_service(frpc_path: Optional[str] = None) -> None:
    pf.start_service(frpc_path)


def stop_service(frpc_path: Optional[str] = None) -> None:
    pf.stop_service(frpc_path)


# ---------------- 开机自启 ----------------
def set_boot_autostart(enable: bool, exe_path: Optional[str] = None) -> None:
    pf.set_boot_autostart(enable, exe_path)


def is_boot_autostart() -> bool:
    return pf.is_boot_autostart()
