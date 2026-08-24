"""frpc 运行日志解析。

状态来源不再是 admin API，而是 frpc 的运行日志——通过解析日志判断：
- 是否成功登录服务器（login to server success / failed）；
- 每个代理是否成功创建隧道（[name] start proxy success / error）。

frpc 日志关键行示例（v0.70.x）：
    [I] [service.go:311] [abc123] login to server success, get run id [abc123], server udp port [0]
    [I] [control.go:180] [abc123] [ssh] start proxy success
    [W] [service.go:322] connect to server error: dial tcp ...
    login to the server failed: dial tcp ...

日志由 frpc 自身写入（配置 log.to 指向数据目录下的 frpc.log），本模块只读。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# 登录 / 隧道状态关键字
_LOGIN_OK_RE = re.compile(r"login to server success")
# 失败有两种形态：
#   - log.to 文件内（日志框架 [W]）：connect to server error: ...
#   - stdout/stderr（frpc 退出前 fmt.Println）：login to the server failed: ...
_LOGIN_FAIL_RE = re.compile(r"login to (?:the )?server failed|connect to server error")
_LOGIN_FAIL_MSG_RE = re.compile(r"(?:login to (?:the )?server failed|connect to server error)[:\s]*(.*)")
# 代理名形如 [ssh] start proxy success  —— 匹配紧邻关键字的那个方括号
_PROXY_OK_RE = re.compile(r"\[([^\]]+)\]\s+start proxy success")
_PROXY_ERR_RE = re.compile(r"\[([^\]]+)\]\s+start proxy error[:\s]*(.*)")

# 只解析日志尾部，避免大文件全量扫描
_TAIL_LINES = 2000


def _tail_lines(path: Path, n: int = _TAIL_LINES) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    return lines[-n:] if len(lines) > n else lines


def parse(log_path: str | Path) -> dict[str, Any]:
    """解析日志文件，返回：
    {
        "login_ok": True/False/None,   # None 表示尚未出现登录结果（未知）
        "login_msg": str,              # 最近一次登录失败的原因
        "proxies": {name: {"status": "running"|"error", "err": str}},
    }
    """
    result: dict[str, Any] = {"login_ok": None, "login_msg": "", "proxies": {}}
    for line in _tail_lines(Path(log_path)):
        if _LOGIN_OK_RE.search(line):
            result["login_ok"] = True
            continue
        m = _LOGIN_FAIL_RE.search(line)
        if m:
            result["login_ok"] = False
            mm = _LOGIN_FAIL_MSG_RE.search(line)
            result["login_msg"] = mm.group(1).strip() if mm else ""
            continue
        m = _PROXY_OK_RE.search(line)
        if m:
            result["proxies"][m.group(1)] = {"status": "running", "err": ""}
            continue
        m = _PROXY_ERR_RE.search(line)
        if m:
            result["proxies"][m.group(1)] = {"status": "error", "err": m.group(2).strip()}
    return result
