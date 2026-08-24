"""frpc 配置数据模型与 TOML 读写。

负责加载 / 保存 frpc.toml，封装 server、auth、admin、log 等公共段，
以及 [[proxies]] 规则数组。未知字段在往返写入时会被保留。
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "frpc.toml"

# frpc 现代 TOML 字段名（与官方一致，便于直接对接 frpc）
SERVER_KEYS = ("serverAddr", "serverPort", "user", "tls.enable", "tls.certFile", "tls.keyFile", "tls.trustedCaFile", "dnsServer", "protocol", "loginFailExit", "heartbeatInterval", "heartbeatTimeout", "metadatas")
AUTH_KEYS = ("token", "method", "additional", "oidc.clientId", "oidc.clientSecret", "oidc.audience", "oidc.tokenEndpoint")
ADMIN_KEYS = ("adminAddr", "adminPort", "adminUser", "adminPwd")
LOG_KEYS = ("to", "level", "maxDays", "disablePrintColor")

DEFAULT_ADMIN_ADDR = "127.0.0.1"
DEFAULT_ADMIN_PORT = 7400
DEFAULT_ADMIN_USER = "admin"


def _toml_value(v: Any) -> str:
    """将单个值渲染为 TOML 字面量。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, list):
        return "[ " + ", ".join(_toml_value(x) for x in v) + " ]"
    if v is None:
        return '""'
    return f'"{v}"'


def _emit_kv(prefix: str, d: dict[str, Any]):
    """递归展开为 dotted key = value 行。"""
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict) and v:
            yield from _emit_kv(full, v)
        else:
            yield f"{full} = {_toml_value(v)}"


def _dump_toml(data: dict[str, Any]) -> str:
    """生成 frpc 兼容的 TOML。

    说明：TOML 的 [table] 段会一直保持“打开”状态，直到下一个表头；若在其中插入
    顶层标量键会被错误归入该表。因此这里把嵌套 dict 统一展开为顶层 dotted key
    （如 auth.token = "..."），仅 [[proxies]] 使用数组表，与 frpc 官方配置风格一致。
    """
    lines: list[str] = []
    proxies = data.get("proxies") if isinstance(data.get("proxies"), list) else None
    rest = {k: v for k, v in data.items() if k != "proxies"}

    for k, v in rest.items():
        if isinstance(v, dict) and v:
            # 以 dotted key 形式展开到顶层，避免 [table] 作用域污染
            lines.extend(_emit_kv(k, v))
        else:
            lines.append(f"{k} = {_toml_value(v)}")
    if lines:
        lines.append("")

    if proxies:
        for pr in proxies:
            lines.append("[[proxies]]")
            lines.extend(_emit_kv("", pr))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class FrpcConfig:
    """frpc.toml 的内存表示。``data`` 保存完整 TOML 字典，便于往返保留用户自定义字段。"""

    def __init__(self, data: dict[str, Any] | None = None):
        self.data: dict[str, Any] = dict(data) if data else {}

    # ---------- 加载 / 保存 ----------
    @classmethod
    def load(cls, path: str | Path) -> "FrpcConfig":
        p = Path(path)
        if not p.exists():
            return cls()
        with p.open("rb") as f:
            data = tomllib.load(f)
        return cls(data)

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_dump_toml(self.data), encoding="utf-8")

    # ---------- server / auth ----------
    def get_server_addr(self) -> str:
        return str(self.data.get("serverAddr", ""))

    def get_server_port(self) -> int:
        return int(self.data.get("serverPort", 0) or 0)

    def get_auth_token(self) -> str:
        auth = self.data.get("auth")
        if isinstance(auth, dict):
            return str(auth.get("token", ""))
        return ""

    def set_common(self, *, server_addr: str, server_port: int, auth_token: str) -> None:
        self.data["serverAddr"] = server_addr.strip()
        self.data["serverPort"] = int(server_port)
        if auth_token:
            auth = self.data.setdefault("auth", {})
            if not isinstance(auth, dict):
                auth = {}
                self.data["auth"] = auth
            auth["token"] = auth_token

    # ---------- admin ----------
    def ensure_admin(self, *, port: int = DEFAULT_ADMIN_PORT, user: str = DEFAULT_ADMIN_USER, pwd: str = "") -> None:
        """确保 admin 服务存在，便于本程序通过 admin API 轮询状态。"""
        self.data.setdefault("adminAddr", DEFAULT_ADMIN_ADDR)
        self.data.setdefault("adminPort", int(port))
        self.data.setdefault("adminUser", user)
        if pwd:
            self.data["adminPwd"] = pwd
        elif "adminPwd" not in self.data:
            self.data["adminPwd"] = ""

    def get_admin(self) -> dict[str, Any]:
        return {
            "addr": str(self.data.get("adminAddr", DEFAULT_ADMIN_ADDR)),
            "port": int(self.data.get("adminPort", DEFAULT_ADMIN_PORT)),
            "user": str(self.data.get("adminUser", DEFAULT_ADMIN_USER)),
            "pwd": str(self.data.get("adminPwd", "")),
        }

    # ---------- log / 运行时默认值 ----------
    def ensure_log(self, *, log_path: str) -> None:
        """确保 frpc 后台常驻运行所需的默认值：

        - log.to 指向日志文件（供状态解析）；
        - loginFailExit=false：连接失败时持续重试而非秒退（frpc 新版默认 true，
          连不上服务器会立即退出，导致「秒退进程/停止失效/启动闪现」）。
        setdefault 不覆盖用户已有设置。
        """
        log = self.data.get("log")
        if not isinstance(log, dict):
            log = {}
            self.data["log"] = log
        log.setdefault("to", log_path)
        log.setdefault("level", "info")
        log.setdefault("maxDays", 3)
        # 关键：连接失败持续重试，避免 frpc 秒退
        self.data.setdefault("loginFailExit", False)

    # ---------- proxies ----------
    def get_proxies(self) -> list[dict[str, Any]]:
        proxies = self.data.get("proxies")
        if not isinstance(proxies, list):
            return []
        return proxies

    def add_proxy(self, proxy: dict[str, Any]) -> None:
        self.data.setdefault("proxies", []).append(proxy)

    def remove_proxy(self, name: str) -> bool:
        before = len(self.get_proxies())
        self.data["proxies"] = [p for p in self.get_proxies() if p.get("name") != name]
        return len(self.get_proxies()) != before

    def find_proxy(self, name: str) -> dict[str, Any] | None:
        for p in self.get_proxies():
            if p.get("name") == name:
                return p
        return None

    def is_name_taken(self, name: str, exclude: str | None = None) -> bool:
        for p in self.get_proxies():
            if p.get("name") == name and name != exclude:
                return True
        return False

    # ---------- 校验公共段 ----------
    def validate_common(self) -> list[str]:
        errors: list[str] = []
        if not self.get_server_addr():
            errors.append("服务器地址(serverAddr)不能为空")
        if not (1 <= self.get_server_port() <= 65535):
            errors.append("服务器端口(serverPort)必须在 1-65535 之间")
        return errors
