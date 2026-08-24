"""Python ↔ JS 桥接层（pywebview js_api）。

把已验证的后端（config / validation / controller / status）封装为前端可直接调用的
同步方法。每个方法返回可被 JSON 序列化的 dict / list，pywebview 会自动在 JS 侧包装为
Promise。前端通过 `window.pywebview.api.<method>(...)` 调用。

为杜绝历史「轮询拉起进程」问题：service_status 与 frpc 版本均做带 TTL 的本地缓存，
平均至多每数秒才真正调用一次 sc / frpc 子进程；tasklist 仅在判断前台进程时调用。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import config as cfg
import validation as val
import controller as ctrl
import logstatus
import platforms as pf

# 轮询期间严禁频繁拉起子进程：探测全部「缓存到失效」，仅在用户操作（start/stop/toggle/检查状态）
# 后才刷新。开机自启(boot)/版本(version)/服务(svc) 在本程序生命周期内不变或仅由用户操作改变，
# 因此绝不在轮询中周期性拉起 reg.exe / sc.exe / frpc --version。
PROC_CACHE_TTL = 10.0     # 前台进程存活探测缓存秒数（仅在有前台进程时才查 tasklist）
LATENCY_CACHE_TTL = 5.0   # 服务器延迟（TCP 握手）缓存秒数


class FrpApi:
    def __init__(self, config_path: str | Path, frpc_path: Optional[str]):
        self.config_path = Path(config_path)
        self.frpc_path = frpc_path
        self._svc_cache: Optional[str] = None
        self._svc_cache_ts = 0.0
        self._version_cache: Optional[str] = None
        self._version_cache_ts = 0.0
        self._boot_cache: Optional[bool] = None
        self._boot_cache_ts = 0.0
        self._proc_cache: Optional[bool] = None
        self._proc_cache_ts = 0.0
        self._latency_cache: Optional[int] = None
        self._latency_cache_ts = 0.0
        self.cfg = cfg.FrpcConfig.load(self.config_path)

    # ---------- 内部工具 ----------
    def _service_status_cached(self) -> str:
        # 服务状态只会在本程序的 启停/安装/卸载 操作后改变，因此缓存到失效即可，
        # 轮询期间不再主动拉起 sc.exe。check_mgmt / start / stop / toggle_service 会清缓存。
        if self._svc_cache is None:
            self._svc_cache = ctrl.service_status()
        return self._svc_cache

    def _boot_cached(self) -> bool:
        # 开机自启状态只会由 toggle_boot 改变，缓存到失效即可，轮询期间绝不拉 reg query
        if self._boot_cache is None:
            self._boot_cache = ctrl.is_boot_autostart()
        return self._boot_cache

    def _process_running_cached(self) -> bool:
        now = time.time()
        if self._proc_cache is None or now - self._proc_cache_ts > PROC_CACHE_TTL:
            self._proc_cache = ctrl.process_running(str(self.config_path))
            self._proc_cache_ts = now
        return self._proc_cache

    def _version_cached(self) -> str:
        # frpc 版本在本程序生命周期内不变，只查一次后缓存到失效（不再周期性拉 frpc --version）
        if self._version_cache is None:
            try:
                self._version_cache = ctrl.get_frpc_version(self.frpc_path) if self.frpc_path else "未找到 frpc"
            except Exception:
                self._version_cache = "未知"
        return self._version_cache

    def _latency_cached(self) -> Optional[int]:
        # 服务器延迟用 TCP 握手计时，带短 TTL 缓存；不可达返回 None（无子进程，不阻塞轮询）
        now = time.time()
        if self._latency_cache_ts == 0.0 or now - self._latency_cache_ts > LATENCY_CACHE_TTL:
            addr = self.cfg.get_server_addr()
            port = self.cfg.get_server_port()
            self._latency_cache = pf.measure_tcp_latency(addr, port)
            self._latency_cache_ts = now
        return self._latency_cache

    def _save_config(self) -> None:
        # 确保 frpc 把运行日志写到数据目录，供 logstatus 解析状态（不再依赖 admin API）
        self.cfg.ensure_log(log_path=str(pf.data_dir() / "frpc.log"))
        self.cfg.save(self.config_path)

    # ---------- 状态聚合（前端轮询入口） ----------
    def get_state(self) -> dict[str, Any]:
        self.cfg = cfg.FrpcConfig.load(self.config_path)
        # 状态来源：frpc 运行日志（是否成功登录 / 成功创建隧道），而非 admin API
        ls = logstatus.parse(pf.data_dir() / "frpc.log")

        version = self._version_cached()

        svc = self._service_status_cached()
        proc_running = self._process_running_cached()
        running = (svc == "running") or proc_running

        # 运行状态：只由进程/服务是否存活决定（不依赖日志，避免停止后旧日志误判为「运行中」）
        if svc == "running":
            run_text, run_ok = "服务运行中", True
        elif svc == "stopped":
            run_text, run_ok = "服务已停止", False
        elif proc_running:
            run_text, run_ok = "进程运行中", True
        else:
            run_text, run_ok = "已停止", False

        # 连接状态：以「frpc 是否在运行」为前提，运行中才看日志登录结果
        if not running:
            link_text, link_ok, link_warn = "未连接", False, False
        elif ls["login_ok"] is True:
            link_text, link_ok, link_warn = "已连接", True, False
        elif ls["login_ok"] is False:
            link_text, link_ok, link_warn = "未连接", False, False
        else:
            link_text, link_ok, link_warn = "连接中", False, True

        proxies: list[dict[str, Any]] = []
        live_map = ls["proxies"]
        for p in self.cfg.get_proxies():
            name = str(p.get("name", ""))
            ptype = str(p.get("type", ""))
            local = f"{p.get('localIP', '')}:{p.get('localPort', '')}"
            if ptype in ("http", "https"):
                cd = p.get("customDomains")
                remote = ",".join(cd) if isinstance(cd, list) else str(cd or p.get("subdomain", ""))
            else:
                remote = str(p.get("remotePort", ""))
            # frpc 未运行时，日志里的旧隧道状态不再可信，一律视为「未运行」
            lv = live_map.get(name) if running else None
            if lv and lv.get("status") == "running":
                status, tag = "运行中", "ok"
            elif lv and lv.get("status") == "error":
                status, tag = lv.get("err") or "启动失败", "bad"
            else:
                status, tag = "未运行", "idle"
            proxies.append({"name": name, "type": ptype, "local": local,
                            "remote": remote, "status": status, "tag": tag})

        return {
            "version": version,
            "link": {"text": link_text, "ok": link_ok, "warn": link_warn},
            "run": {"text": run_text, "ok": run_ok},
            "latency": {"ms": self._latency_cached()},
            "proxies": proxies,
            "service": {"installed": svc != "not_installed", "state": svc},
            "boot": self._boot_cached(),
            "config": {
                "addr": self.cfg.get_server_addr(),
                "port": self.cfg.get_server_port(),
                "token": self.cfg.get_auth_token(),
            },
            "frpc_found": bool(self.frpc_path),
        }

    # ---------- 规则校验 / 增删改 ----------
    def validate_proxy(self, proxy: dict[str, Any], exclude_name: Optional[str] = None) -> list[dict[str, str]]:
        existing = [p for p in self.cfg.get_proxies() if p.get("name") != exclude_name]
        return val.validate_proxy(proxy, existing)

    def get_proxy(self, name: str) -> Optional[dict[str, Any]]:
        self.cfg = cfg.FrpcConfig.load(self.config_path)
        return self.cfg.find_proxy(name)

    def add_proxy(self, proxy: dict[str, Any]) -> dict[str, Any]:
        # 规范化类型（前端 input 取出来是字符串，localPort/remotePort 必须是 int）
        proxy = val.normalize_proxy(proxy)
        errs = self.validate_proxy(proxy)
        if errs:
            return {"ok": False, "errors": errs}
        self.cfg.add_proxy(proxy)
        self._save_config()
        return {"ok": True}

    def update_proxy(self, old_name: str, proxy: dict[str, Any]) -> dict[str, Any]:
        proxy = val.normalize_proxy(proxy)
        errs = self.validate_proxy(proxy, exclude_name=old_name)
        if errs:
            return {"ok": False, "errors": errs}
        self.cfg.remove_proxy(old_name)
        self.cfg.add_proxy(proxy)
        self._save_config()
        return {"ok": True}

    def delete_proxy(self, name: str) -> dict[str, Any]:
        self.cfg.remove_proxy(name)
        self._save_config()
        return {"ok": True}

    # ---------- 启动 / 停止 / 重载 ----------
    def start(self) -> dict[str, Any]:
        if not self.frpc_path:
            return {"ok": False, "msg": "未找到 frpc，请重新运行程序以释出内置二进制。"}
        # 确保配置里已写入日志路径，frpc 才能把运行日志落盘供状态解析
        self.cfg = cfg.FrpcConfig.load(self.config_path)
        self.cfg.ensure_log(log_path=str(pf.data_dir() / "frpc.log"))
        self.cfg.save(self.config_path)
        svc = self._service_status_cached()
        try:
            if svc != "not_installed":
                try:
                    ctrl.stop_service(self.frpc_path)
                except RuntimeError:
                    pass
            else:
                ctrl.stop_process(str(self.config_path))
            if svc != "not_installed":
                ctrl.start_service(self.frpc_path)
            else:
                ctrl.start_process(self.frpc_path, str(self.config_path))
        except RuntimeError as e:
            return {"ok": False, "msg": f"需要管理员权限或参数错误：\n{e}"}
        self._svc_cache = None
        self._proc_cache = None
        return {"ok": True}

    def stop(self) -> dict[str, Any]:
        svc = self._service_status_cached()
        try:
            if svc != "not_installed":
                ctrl.stop_service(self.frpc_path)
            else:
                ctrl.stop_process(str(self.config_path))
        except RuntimeError as e:
            return {"ok": False, "msg": str(e)}
        self._svc_cache = None
        self._proc_cache = None
        return {"ok": True}

    def reload_config(self) -> dict[str, Any]:
        self.stop()
        time.sleep(0.3)
        return self.start()

    # ---------- 设置保存 ----------
    def save_common(self, addr: str, port: Any, token: str) -> dict[str, Any]:
        try:
            port = int(port)
        except (ValueError, TypeError):
            return {"ok": False, "msg": "服务器端口必须为整数"}
        self.cfg.set_common(server_addr=addr, server_port=port, auth_token=token)
        self._save_config()
        return {"ok": True}

    # ---------- 运行管理开关 ----------
    def toggle_service(self, enable: bool) -> dict[str, Any]:
        if not self.frpc_path:
            return {"ok": False, "msg": "未找到 frpc，无法安装系统服务。"}
        try:
            if enable:
                ctrl.install_service(self.frpc_path, str(self.config_path))
            else:
                ctrl.uninstall_service(self.frpc_path)
        except RuntimeError as e:
            return {"ok": False, "msg": f"请以管理员身份运行本程序：\n{e}"}
        self._svc_cache = None
        self._proc_cache = None
        return {"ok": True}

    def toggle_boot(self, enable: bool) -> dict[str, Any]:
        try:
            ctrl.set_boot_autostart(enable, exe_path=os.path.abspath(sys.argv[0]))
        except RuntimeError as e:
            return {"ok": False, "msg": str(e)}
        self._boot_cache = None
        return {"ok": True}

    # ---------- 杂项 ----------
    def check_mgmt(self) -> dict[str, Any]:
        self._svc_cache = None
        self._boot_cache = None
        self._proc_cache = None
        return self.get_state()

    # ---------- 日志 ----------
    def get_log(self, lines: int = 500) -> dict[str, Any]:
        """读取 frpc 运行日志（数据目录 frpc.log），返回尾部若干行。"""
        log_path = pf.data_dir() / "frpc.log"
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {"log": "", "path": str(log_path), "total_lines": 0, "exists": False}
        all_lines = text.splitlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return {"log": "\n".join(tail), "path": str(log_path),
                "total_lines": len(all_lines), "exists": True}

    def clear_log(self) -> dict[str, Any]:
        """清空日志文件（便于重新观察）。"""
        log_path = pf.data_dir() / "frpc.log"
        try:
            log_path.write_text("", encoding="utf-8")
        except OSError as e:
            return {"ok": False, "msg": f"清空失败：{e}"}
        return {"ok": True}

    def open_log_dir(self) -> dict[str, Any]:
        """在系统文件管理器中打开日志所在文件夹（并尽量选中日志文件）。"""
        log_path = pf.data_dir() / "frpc.log"
        try:
            if pf.IS_WINDOWS:
                subprocess.run(["explorer", "/select,", str(log_path)])
            elif pf.IS_MACOS:
                subprocess.run(["open", "-R", str(log_path)])
            else:
                subprocess.run(["xdg-open", str(pf.data_dir())])
        except Exception as e:
            return {"ok": False, "msg": f"打开失败：{e}"}
        return {"ok": True}
