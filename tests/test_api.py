import os
import tempfile
from pathlib import Path
from unittest import mock

import api_bridge as api

HERE = Path(__file__).parent.parent


def _make_api():
    # 用项目内临时目录，不自动删除（避免沙箱 safe-delete 在 cleanup 阶段拦截）
    d = Path(tempfile.mkdtemp(prefix="api_", dir=HERE))
    return api.FrpApi(d / "frpc.toml", None)


def test_get_state_structure():
    s = _make_api().get_state()
    for key in ("version", "link", "run", "proxies", "service", "boot", "config", "frpc_found"):
        assert key in s, f"get_state 缺少字段 {key}"
    assert isinstance(s["proxies"], list)
    assert isinstance(s["service"], dict) and "installed" in s["service"]
    assert isinstance(s["config"], dict) and "addr" in s["config"]


def test_validate_proxy_rejects_empty_name():
    errs = _make_api().validate_proxy({"name": "", "type": "tcp", "localIP": "127.0.0.1",
                                        "localPort": "22", "remotePort": "6000"})
    assert any(e["field"] == "name" for e in errs)


def test_validate_proxy_ok_tcp():
    errs = _make_api().validate_proxy({"name": "ssh", "type": "tcp", "localIP": "127.0.0.1",
                                        "localPort": 22, "remotePort": 6000})
    assert errs == [], f"合法 tcp 规则不应报错，但得到 {errs}"


def test_add_get_update_delete_proxy():
    a = _make_api()
    r = a.add_proxy({"name": "ssh", "type": "tcp", "localIP": "127.0.0.1",
                     "localPort": 22, "remotePort": 6000})
    assert r["ok"], r
    p = a.get_proxy("ssh")
    assert p and p["name"] == "ssh" and p["localPort"] == 22
    r2 = a.update_proxy("ssh", {"name": "ssh", "type": "tcp", "localIP": "127.0.0.1",
                                "localPort": 2222, "remotePort": 6000})
    assert r2["ok"], r2
    assert a.get_proxy("ssh")["localPort"] == 2222
    assert a.delete_proxy("ssh")["ok"]
    assert a.get_proxy("ssh") is None


def test_save_common():
    a = _make_api()
    assert a.save_common("frp.example.com", "7000", "tok123")["ok"]
    a2 = api.FrpApi(a.config_path, None)
    st = a2.get_state()
    assert st["config"]["addr"] == "frp.example.com"
    assert st["config"]["port"] == 7000


def test_get_state_uses_log_not_admin(monkeypatch):
    """状态来自运行日志：登录成功→已连接，隧道成功→运行中。"""
    d = Path(tempfile.mkdtemp(prefix="api_", dir=HERE))
    (d / "frpc.log").write_text(
        "2026-08-24 03:57:14.000 [I] [service.go:311] [abc123] login to server success, get run id [abc123], server udp port [0]\n"
        "2026-08-24 03:57:14.000 [I] [control.go:180] [abc123] [ssh] start proxy success\n",
        encoding="utf-8")
    (d / "frpc.toml").write_text(
        'serverAddr = "x"\nserverPort = 7000\n'
        '[[proxies]]\nname = "ssh"\ntype = "tcp"\nlocalIP = "127.0.0.1"\nlocalPort = 22\nremotePort = 6000\n',
        encoding="utf-8")

    monkeypatch.setattr(api.pf, "data_dir", lambda: d)
    monkeypatch.setattr(api.ctrl, "service_status", lambda: "not_installed")
    monkeypatch.setattr(api.ctrl, "process_running", lambda p: True)  # 模拟进程在运行
    monkeypatch.setattr(api.ctrl, "is_boot_autostart", lambda: False)
    monkeypatch.setattr(api.ctrl, "get_frpc_version", lambda p: "0.70.1")

    a = api.FrpApi(d / "frpc.toml", "C:/dummy/frpc.exe")
    s = a.get_state()
    assert s["link"]["text"] == "已连接"
    assert s["proxies"][0]["status"] == "运行中"
    assert s["proxies"][0]["tag"] == "ok"


def test_get_state_stopped_ignores_stale_log(monkeypatch):
    """停止后（进程不在运行），旧日志里的 login success / start proxy success 不得再误判为运行中。"""
    d = Path(tempfile.mkdtemp(prefix="api_", dir=HERE))
    (d / "frpc.log").write_text(
        "2026-08-24 03:57:14.000 [I] [service.go:311] [abc123] login to server success, get run id [abc123]\n"
        "2026-08-24 03:57:14.000 [I] [control.go:180] [abc123] [ssh] start proxy success\n",
        encoding="utf-8")
    (d / "frpc.toml").write_text(
        'serverAddr = "x"\nserverPort = 7000\n'
        '[[proxies]]\nname = "ssh"\ntype = "tcp"\nlocalIP = "127.0.0.1"\nlocalPort = 22\nremotePort = 6000\n',
        encoding="utf-8")

    monkeypatch.setattr(api.pf, "data_dir", lambda: d)
    monkeypatch.setattr(api.ctrl, "service_status", lambda: "not_installed")
    monkeypatch.setattr(api.ctrl, "process_running", lambda p: False)  # 进程已停
    monkeypatch.setattr(api.ctrl, "is_boot_autostart", lambda: False)
    monkeypatch.setattr(api.ctrl, "get_frpc_version", lambda p: "0.70.1")

    a = api.FrpApi(d / "frpc.toml", "C:/dummy/frpc.exe")
    s = a.get_state()
    assert s["run"]["text"] == "已停止"
    assert s["link"]["text"] == "未连接"
    assert s["proxies"][0]["status"] == "未运行"


def test_start_without_frpc_does_not_launch():
    a = _make_api()
    with mock.patch.object(api.ctrl, "stop_process") as mstop, \
         mock.patch.object(api.ctrl, "start_process", return_value=123) as mstart:
        r = a.start()
        assert r["ok"] is False
        mstart.assert_not_called()


def test_get_log_and_clear(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="api_", dir=HERE))
    (d / "frpc.log").write_text("line1\nline2\nline3\n", encoding="utf-8")
    monkeypatch.setattr(api.pf, "data_dir", lambda: d)
    a = api.FrpApi(d / "frpc.toml", None)
    r = a.get_log()
    assert r["log"] == "line1\nline2\nline3"
    assert r["total_lines"] == 3
    assert r["exists"] is True
    assert a.clear_log()["ok"]
    r2 = a.get_log()
    assert r2["log"] == ""
    assert r2["total_lines"] == 0


def test_get_log_missing_file(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="api_", dir=HERE))
    monkeypatch.setattr(api.pf, "data_dir", lambda: d)
    a = api.FrpApi(d / "frpc.toml", None)
    r = a.get_log()
    assert r["exists"] is False
    assert r["log"] == ""


def test_add_proxy_normalizes_port_types():
    """前端 input 取出来是字符串，add_proxy 必须规范为 int，否则 frpc 报
    `cannot unmarshal string into localPort of type int`（回归测试）。"""
    d = Path(tempfile.mkdtemp(prefix="api_", dir=HERE))
    a = api.FrpApi(d / "frpc.toml", None)
    r = a.add_proxy({"name": "x", "type": "tcp", "localIP": "127.0.0.1",
                     "localPort": "22", "remotePort": "6000"})
    assert r["ok"], r
    text = (d / "frpc.toml").read_text(encoding="utf-8")
    assert "localPort = 22" in text and 'localPort = "22"' not in text
    assert "remotePort = 6000" in text and 'remotePort = "6000"' not in text
