"""跨平台运行时抽象层。

把与操作系统强相关的行为（数据目录、frpc 二进制定位与释出、服务管理、开机自启、
进程管理、后台启动方式、WebView 后端选择）统一收敛到本模块，使 controller / 入口 /
桥接层与平台无关，从而支持 Windows 与 Linux（macOS 预留）发行。

设计约定：
- 数据目录统一存放：配置 frpc.toml、内置释出的 frpc 二进制、pid 文件、版本标记。
- Windows：%APPDATA%\\frpC ；Linux：$XDG_CONFIG_HOME/frpC（缺省 ~/.config/frpC）。
- 服务管理：Windows 用 frpc 自带的 install/start/stop + sc；Linux 用 systemd 用户单元。
- 开机自启：Windows 写 HKCU Run 键；Linux 写 ~/.config/autostart/*.desktop。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

IS_WINDOWS = (sys.platform == "win32") or (os.name == "nt")
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"

APP_DIR_NAME = "frpC"
SERVICE_NAME = "frpc"
RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "FrpGui"
FRPC_VERSION_MARKER = "frpc.version"


# ======================================================================
# 数据目录 / 路径
# ======================================================================
def _config_home() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def data_dir() -> Path:
    """本程序的数据目录（配置 + frpc 二进制 + pid + 版本标记统一存放）。"""
    d = _config_home() / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return data_dir() / "frpc.toml"


def frpc_binary_name() -> str:
    return "frpc.exe" if IS_WINDOWS else "frpc"


def frpc_runtime_path() -> Path:
    return data_dir() / frpc_binary_name()


def resource_dir() -> Path:
    """打包/源码运行时读取内置资源的目录。

    PyInstaller 单文件模式：资源被解压到 sys._MEIPASS；
    源码运行：读取仓库 vendor/frpc 目录。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parent.parent / "vendor" / "frpc"


def bundled_frpc_path() -> Optional[Path]:
    p = resource_dir() / frpc_binary_name()
    return p if p.exists() else None


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def _ensure_executable(p: Path) -> None:
    if not IS_WINDOWS:
        try:
            p.chmod(p.stat().st_mode | 0o111)
        except OSError:
            pass


def extract_frpc() -> Optional[Path]:
    """把内置 frpc 二进制释出到数据目录，返回可用路径；无内置则返回 None。

    - 若数据目录已存在且与内置内容一致（按 sha256 判断），直接复用；
    - 否则覆盖并写入版本标记，保证升级后能用到新二进制。
    """
    bundled = bundled_frpc_path()
    dest = frpc_runtime_path()
    if bundled is None:
        return dest if dest.exists() else None

    if dest.exists():
        try:
            if _sha256(dest) == _sha256(bundled):
                _ensure_executable(dest)
                return dest
        except OSError:
            pass

    try:
        shutil.copy2(bundled, dest)
    except OSError:
        # 数据目录写入失败时，回退为直接使用内置资源（只读运行）
        _ensure_executable(bundled)
        return bundled
    _ensure_executable(dest)
    marker = resource_dir() / FRPC_VERSION_MARKER
    if marker.exists():
        try:
            shutil.copy2(marker, data_dir() / FRPC_VERSION_MARKER)
        except OSError:
            pass
    return dest


# ======================================================================
# 子进程辅助
# ======================================================================
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_kwargs() -> dict:
    """Windows GUI 进程里启动 console 子程序时抑制控制台窗口闪现。

    frpC 本身是窗口程序（无控制台），spawn sc/reg/tasklist/taskkill/frpc 等 console
    子程序时若不设 CREATE_NO_WINDOW，Windows 会为它们新开控制台窗口，表现为「按按钮弹闪 cmd」。
    """
    return {"creationflags": _CREATE_NO_WINDOW} if IS_WINDOWS else {}


def _run(cmd: list[str], admin_ok: bool = False) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", **_run_kwargs())
    except FileNotFoundError as e:
        raise RuntimeError(f"未找到可执行文件：{cmd[0]}") from e
    if proc.returncode != 0 and not admin_ok:
        msg = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(msg or f"命令执行失败：{' '.join(cmd)}")
    return proc


# ======================================================================
# 前台进程管理
# ======================================================================
def _pid_file(config_path: str | Path) -> Path:
    return Path(config_path).with_suffix(".pid")


def start_process(frpc_path: str, config_path: str | Path) -> int:
    """以前台进程方式启动 frpc，返回 PID。stdout/stderr 追加到数据目录日志文件。"""
    popen_kwargs: dict = {}
    if IS_WINDOWS:
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_kwargs["start_new_session"] = True
    # 日志落盘：供 logstatus 解析状态（frpc 自身也会因配置 log.to 写日志，此处兜底）
    log_path = data_dir() / "frpc.log"
    with open(log_path, "ab") as logf:
        proc = subprocess.Popen(
            [frpc_path, "-c", str(config_path)],
            stdout=logf,
            stderr=subprocess.STDOUT,
            **popen_kwargs,
        )
    _pid_file(config_path).write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            **_run_kwargs(),
        )
        return str(pid) in proc.stdout
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return True
    except OSError:
        return False


def stop_process(config_path: str | Path) -> None:
    pidf = _pid_file(config_path)
    if not pidf.exists():
        return
    try:
        pid = int(pidf.read_text().strip())
    except ValueError:
        pid = 0
    if pid and is_pid_alive(pid):
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                           capture_output=True, **_run_kwargs())
        else:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.kill(pid, sig)
                except OSError:
                    break
                break
    pidf.unlink(missing_ok=True)


def process_running(config_path: str | Path) -> bool:
    pidf = _pid_file(config_path)
    if not pidf.exists():
        return False
    try:
        pid = int(pidf.read_text().strip())
    except ValueError:
        return False
    return is_pid_alive(pid)


# ======================================================================
# 系统服务
# ======================================================================
def _systemd_unit_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "systemd" / "user"


def _systemd_unit_content(frpc_path: str, config_path: str | Path) -> str:
    return (
        "[Unit]\n"
        "Description=frpc reverse proxy client\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f'ExecStart="{frpc_path}" -c "{config_path}"\n'
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def service_status() -> str:
    """返回 running / stopped / not_installed / unknown。"""
    if IS_WINDOWS:
        proc = subprocess.run(
            ["sc", "query", SERVICE_NAME],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            **_run_kwargs(),
        )
        out = proc.stdout or ""
        if "STATE" in out:
            if "RUNNING" in out:
                return "running"
            if "STOPPED" in out:
                return "stopped"
            return "unknown"
        return "not_installed"
    # Linux：systemd 用户单元
    proc = subprocess.run(
        ["systemctl", "--user", "is-active", SERVICE_NAME],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (proc.stdout or "").strip()
    if out == "active":
        return "running"
    if out in ("inactive", "failed"):
        return "stopped"
    return "not_installed"


def install_service(frpc_path: str, config_path: str | Path) -> None:
    if IS_WINDOWS:
        _run([frpc_path, "install", "-c", str(config_path)])
        try:
            _run([frpc_path, "start"])
        except RuntimeError:
            pass
        return
    unit_dir = _systemd_unit_path()
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / f"{SERVICE_NAME}.service").write_text(
        _systemd_unit_content(frpc_path, config_path), encoding="utf-8")
    try:
        _run(["systemctl", "--user", "daemon-reload"])
    except RuntimeError:
        pass
    # 开启用户 lingering：让 systemd 用户服务在未登录 / 登出后仍保持运行，
    # 否则 --user 服务默认只在用户会话期间存活，达不到"开机自启"的语义。
    try:
        _run(["loginctl", "enable-linger"])
    except RuntimeError:
        pass
    _run(["systemctl", "--user", "enable", "--now", SERVICE_NAME])


def uninstall_service(frpc_path: Optional[str] = None) -> None:
    if IS_WINDOWS:
        if frpc_path:
            try:
                _run([frpc_path, "stop"])
            except RuntimeError:
                pass
        else:
            try:
                _run(["frpc", "stop"])
            except RuntimeError:
                pass
        _run(["sc", "delete", SERVICE_NAME])
        return
    try:
        _run(["systemctl", "--user", "disable", "--now", SERVICE_NAME], admin_ok=True)
    except RuntimeError:
        pass
    unit = _systemd_unit_path() / f"{SERVICE_NAME}.service"
    unit.unlink(missing_ok=True)
    try:
        _run(["systemctl", "--user", "daemon-reload"])
    except RuntimeError:
        pass


def start_service(frpc_path: Optional[str] = None) -> None:
    if IS_WINDOWS:
        _run([frpc_path, "start"] if frpc_path else ["frpc", "start"])
    else:
        _run(["systemctl", "--user", "start", SERVICE_NAME])


def stop_service(frpc_path: Optional[str] = None) -> None:
    if IS_WINDOWS:
        _run([frpc_path, "stop"] if frpc_path else ["frpc", "stop"])
    else:
        _run(["systemctl", "--user", "stop", SERVICE_NAME])


# ======================================================================
# 开机自启
# ======================================================================
def _autostart_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "autostart"


def set_boot_autostart(enable: bool, exe_path: Optional[str] = None) -> None:
    if IS_WINDOWS:
        if enable:
            target = exe_path or sys.executable
            _run(["reg", "add", RUN_KEY, "/v", RUN_VALUE, "/t", "REG_SZ",
                  "/d", f'"{target}" --minimized', "/f"])
        else:
            _run(["reg", "delete", RUN_KEY, "/v", RUN_VALUE, "/f"], admin_ok=True)
        return
    d = _autostart_dir()
    desktop = d / f"{APP_DIR_NAME}.desktop"
    if not enable:
        desktop.unlink(missing_ok=True)
        return
    d.mkdir(parents=True, exist_ok=True)
    target = exe_path or sys.executable
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_DIR_NAME}\n"
        f'Exec="{target}" --minimized\n'
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )


def is_boot_autostart() -> bool:
    if IS_WINDOWS:
        proc = subprocess.run(
            ["reg", "query", RUN_KEY, "/v", RUN_VALUE],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            **_run_kwargs(),
        )
        return proc.returncode == 0 and RUN_VALUE in proc.stdout
    return (_autostart_dir() / f"{APP_DIR_NAME}.desktop").exists()


# ======================================================================
# WebView 后端选择
# ======================================================================
def webview_gui_backend() -> Optional[str]:
    """返回 pywebview 使用的 GUI 后端标识；None 表示交由 pywebview 自动探测。

    - Windows：edgechromium（系统 WebView2，零额外依赖）
    - Linux：gtk（需 PyGObject + WebKit2GTK，由发行包/CI 提供）
    """
    if IS_WINDOWS:
        return "edgechromium"
    if IS_LINUX:
        return "gtk"
    return None


# ======================================================================
# 服务器延迟
# ======================================================================
def measure_tcp_latency(addr: str, port: int, timeout: float = 3.0) -> Optional[int]:
    """测量到 frps 服务器 addr:port 的 TCP 连接往返延迟（毫秒）。

    仅做 TCP 三次握手计时，不依赖 frpc 运行状态、不拉起子进程。
    连接失败（超时/拒绝/DNS 失败）返回 None。
    """
    if not addr or not port:
        return None
    try:
        start = time.perf_counter()
        with socket.create_connection((addr, int(port)), timeout=timeout) as s:
            ms = int((time.perf_counter() - start) * 1000)
        return ms
    except (OSError, ValueError):
        return None
