"""规则校验引擎。

新建 / 编辑规则提交前，必须先经过本模块校验。返回结构化的错误列表，
UI 据此在确认前拦截异常参数、重复名称、重复端口、非法格式等问题。
"""
from __future__ import annotations

import re
from typing import Any

PROXY_TYPES = ("tcp", "udp", "http", "https", "stcp", "xtcp")
TYPE_LABELS = {
    "tcp": "TCP",
    "udp": "UDP",
    "http": "HTTP",
    "https": "HTTPS",
    "stcp": "STCP(私密)",
    "xtcp": "XTCP(点对点)",
}

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


class ValidationError(Exception):
    """携带字段级错误列表的异常。"""

    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("; ".join(e["msg"] for e in errors))


def is_valid_ipv4(value: str) -> bool:
    m = IPV4_RE.match(value.strip())
    if not m:
        return False
    return all(0 <= int(g) <= 255 for g in m.groups())


def is_valid_hostname(value: str) -> bool:
    return bool(DOMAIN_RE.match(value.strip()))


def is_valid_ip_or_host(value: str) -> bool:
    v = value.strip()
    return is_valid_ipv4(v) or is_valid_hostname(v) or v in ("localhost",)


def validate_port(value: Any) -> int | None:
    """返回合法端口整数，否则返回 None。"""
    try:
        port = int(str(value).strip())
    except (ValueError, TypeError):
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _err(field: str, msg: str) -> dict[str, str]:
    return {"field": field, "msg": msg}


def validate_proxy(proxy: dict[str, Any], existing: list[dict[str, Any]]) -> list[dict[str, str]]:
    """校验单条规则。``existing`` 为除本条外的其它已存在规则（用于重复检测）。

    返回错误列表；空列表表示校验通过。
    """
    errors: list[dict[str, str]] = []
    name = str(proxy.get("name", "")).strip()
    ptype = str(proxy.get("type", "")).strip().lower()

    # ---- 名称 ----
    if not name:
        errors.append(_err("name", "规则名称不能为空"))
    elif not NAME_RE.match(name):
        errors.append(_err("name", "名称仅允许字母/数字/下划线/连字符，长度 1-50"))
    elif any(p.get("name") == name for p in existing):
        errors.append(_err("name", f"名称『{name}』已存在，规则名称必须唯一"))

    # ---- 类型 ----
    if not ptype:
        errors.append(_err("type", "必须选择穿透类型"))
    elif ptype not in PROXY_TYPES:
        errors.append(_err("type", f"不支持的类型：{ptype}"))

    # ---- 本地地址 / 端口 ----
    local_ip = str(proxy.get("localIP", "")).strip()
    if not local_ip:
        errors.append(_err("localIP", "本地地址(localIP)不能为空"))
    elif not is_valid_ip_or_host(local_ip):
        errors.append(_err("localIP", f"本地地址格式非法：{local_ip}"))

    local_port = validate_port(proxy.get("localPort"))
    if proxy.get("localPort") in (None, "", ""):
        errors.append(_err("localPort", "本地端口(localPort)不能为空"))
    elif local_port is None:
        errors.append(_err("localPort", f"本地端口必须是不带引号的 1-65535 整数：{proxy.get('localPort')}"))

    # ---- 类型专属 ----
    if ptype in ("tcp", "udp"):
        rp = validate_port(proxy.get("remotePort"))
        if proxy.get("remotePort") in (None, ""):
            errors.append(_err("remotePort", "TCP/UDP 必须填写远程端口(remotePort)"))
        elif rp is None:
            errors.append(_err("remotePort", f"远程端口非法：{proxy.get('remotePort')}"))
        else:
            # 同类型远程端口不可重复
            for p in existing:
                if p.get("type") in ("tcp", "udp") and validate_port(p.get("remotePort")) == rp:
                    errors.append(_err("remotePort", f"远程端口 {rp} 已被规则『{p.get('name')}』占用"))
                    break

    elif ptype in ("http", "https"):
        domains = proxy.get("customDomains") or []
        if isinstance(domains, str):
            domains = [d.strip() for d in domains.split(",") if d.strip()]
        subdomain = str(proxy.get("subdomain", "")).strip()
        if not domains and not subdomain:
            errors.append(_err("customDomains", "HTTP/HTTPS 至少需要 customDomains 或 subdomain 之一"))
        for d in domains:
            if not is_valid_hostname(d):
                errors.append(_err("customDomains", f"域名格式非法：{d}"))
        # 域名全局不可重复
        seen = set()
        for p in existing:
            pd = p.get("customDomains") or []
            if isinstance(pd, str):
                pd = [x.strip() for x in pd.split(",") if x.strip()]
            for d in pd:
                seen.add(d.lower())
        for d in domains:
            if d.lower() in seen:
                errors.append(_err("customDomains", f"域名 {d} 已被规则『{p.get('name')}』使用"))
                break

    elif ptype in ("stcp", "xtcp"):
        sk = str(proxy.get("secretKey", "") or proxy.get("sk", "")).strip()
        if not sk:
            errors.append(_err("secretKey", f"{ptype.upper()} 必须填写密钥(secretKey)"))
        role = str(proxy.get("role", "")).strip().lower()
        if role == "visitor":
            if not str(proxy.get("serverName", "")).strip():
                errors.append(_err("serverName", "visitor 角色必须填写 serverName"))

    return errors


def normalize_proxy(raw: dict[str, Any]) -> dict[str, Any]:
    """将对话框原始字段整理为可写入 TOML 的代理字典（剔除空值、转换类型）。"""
    out: dict[str, Any] = {}
    name = str(raw.get("name", "")).strip()
    if name:
        out["name"] = name
    ptype = str(raw.get("type", "")).strip().lower()
    if ptype:
        out["type"] = ptype

    local_ip = str(raw.get("localIP", "")).strip()
    if local_ip:
        out["localIP"] = local_ip

    lp = validate_port(raw.get("localPort"))
    if lp is not None:
        out["localPort"] = lp

    rp = validate_port(raw.get("remotePort"))
    if rp is not None:
        out["remotePort"] = rp

    domains = raw.get("customDomains")
    if isinstance(domains, str):
        domains = [d.strip() for d in domains.split(",") if d.strip()]
    if isinstance(domains, list) and domains:
        out["customDomains"] = domains

    sub = str(raw.get("subdomain", "")).strip()
    if sub:
        out["subdomain"] = sub

    sk = str(raw.get("secretKey", "") or raw.get("sk", "")).strip()
    if sk:
        out["secretKey"] = sk

    role = str(raw.get("role", "")).strip()
    if role:
        out["role"] = role
        sn = str(raw.get("serverName", "")).strip()
        if sn:
            out["serverName"] = sn

    # frp v0.70.1 v1 schema：useEncryption / useCompression 必须在 transport 子对象里
    transport: dict = {}
    if str(raw.get("useEncryption", "")).strip().lower() in ("1", "true", "yes", "是"):
        transport["useEncryption"] = True
    if str(raw.get("useCompression", "")).strip().lower() in ("1", "true", "yes", "是"):
        transport["useCompression"] = True
    if transport:
        out["transport"] = transport

    # 透传其它自定义字段
    for k, v in raw.items():
        if k in ("name", "type", "localIP", "localPort", "remotePort", "customDomains",
                 "subdomain", "secretKey", "sk", "role", "serverName", "useEncryption",
                 "useCompression", "transport"):
            continue
        if v not in (None, ""):
            out[k] = v
    return out
