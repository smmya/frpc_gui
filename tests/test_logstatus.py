"""logstatus 日志解析测试。"""
import tempfile
from pathlib import Path

import logstatus


def _write(tmp_log: Path, text: str) -> str:
    tmp_log.write_text(text, encoding="utf-8")
    return str(tmp_log)


def test_parse_login_success_and_proxy_states():
    d = Path(tempfile.mkdtemp(prefix="logtest_", dir=Path(__file__).parent.parent))
    log = _write(d / "frpc.log", """\
2026-08-24 03:57:13.619 [I] [sub/root.go:201] start frpc service for config file [x]
2026-08-24 03:57:13.633 [I] [client/service.go:311] try to connect to server...
2026-08-24 03:57:14.000 [I] [service.go:311] [abc123] login to server success, get run id [abc123], server udp port [0]
2026-08-24 03:57:14.000 [I] [proxy_manager.go:144] [abc123] proxy added: [ssh]
2026-08-24 03:57:14.000 [I] [control.go:180] [abc123] [ssh] start proxy success
2026-08-24 03:57:14.000 [E] [control.go:180] [abc123] [web] start proxy error: port already used
""")
    r = logstatus.parse(log)
    assert r["login_ok"] is True
    assert r["proxies"]["ssh"]["status"] == "running"
    assert r["proxies"]["web"]["status"] == "error"
    assert "port already used" in r["proxies"]["web"]["err"]


def test_parse_login_failed_with_reason():
    d = Path(tempfile.mkdtemp(prefix="logtest_", dir=Path(__file__).parent.parent))
    log = _write(d / "frpc.log", """\
2026-08-24 03:57:13.633 [W] [client/service.go:322] connect to server error: dial tcp 127.0.0.1:7000: connectex: No connection could be made
login to the server failed: dial tcp 127.0.0.1:7000: connectex: No connection could be made because the target machine actively refused it.
""")
    r = logstatus.parse(log)
    assert r["login_ok"] is False
    assert "dial tcp" in r["login_msg"]


def test_parse_connect_error_marks_disconnected():
    # log.to 文件内只有 [W] connect to server error（frpc 退出前的 stderr 不进文件）
    d = Path(tempfile.mkdtemp(prefix="logtest_", dir=Path(__file__).parent.parent))
    log = _write(d / "frpc.log", """\
2026-08-24 04:06:00.860 [I] [sub/root.go:201] start frpc service for config file [x]
2026-08-24 04:06:00.878 [I] [client/service.go:311] try to connect to server...
2026-08-24 04:06:00.879 [W] [client/service.go:322] connect to server error: dial tcp 127.0.0.1:7000: connectex: No connection could be made
2026-08-24 04:06:00.879 [I] [sub/root.go:218] frpc service for config file [x] stopped
""")
    r = logstatus.parse(log)
    assert r["login_ok"] is False
    assert "dial tcp" in r["login_msg"]


def test_parse_missing_log_returns_unknown():
    r = logstatus.parse("C:/nonexistent/frpc.log")
    assert r["login_ok"] is None
    assert r["proxies"] == {}


def test_parse_overwrites_with_latest_state():
    # 重连场景：同一代理先 error 后 success，应取最后一次（running）
    d = Path(tempfile.mkdtemp(prefix="logtest_", dir=Path(__file__).parent.parent))
    log = _write(d / "frpc.log", """\
[I] [abc123] [ssh] start proxy error: timeout
[I] [abc123] [ssh] start proxy success
""")
    r = logstatus.parse(log)
    assert r["proxies"]["ssh"]["status"] == "running"
