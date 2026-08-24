import tempfile
from pathlib import Path

import config as cfg


def _sample() -> cfg.FrpcConfig:
    c = cfg.FrpcConfig()
    c.set_common(server_addr="frp.example.com", server_port=7000, auth_token="secret")
    c.ensure_admin(port=7400, user="admin", pwd="pw")
    c.add_proxy({"name": "ssh", "type": "tcp", "localIP": "127.0.0.1", "localPort": 22, "remotePort": 6000})
    return c


def test_roundtrip_toml():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "frpc.toml"
        _sample().save(p)
        text = p.read_text(encoding="utf-8")
        assert "serverAddr" in text and "[[proxies]]" in text
        loaded = cfg.FrpcConfig.load(p)
        assert loaded.get_server_addr() == "frp.example.com"
        assert loaded.get_server_port() == 7000
        assert loaded.get_auth_token() == "secret"
        assert loaded.get_admin()["port"] == 7400
        assert len(loaded.get_proxies()) == 1


def test_preserves_extra_fields():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "frpc.toml"
        c = cfg.FrpcConfig({"serverAddr": "x", "serverPort": 7000, "user": "alice",
                            "proxies": [{"name": "a", "type": "tcp", "localIP": "127.0.0.1",
                                         "localPort": 1, "remotePort": 2, "extraKey": "keep"}]})
        c.save(p)
        loaded = cfg.FrpcConfig.load(p)
        assert loaded.data.get("user") == "alice"
        assert loaded.find_proxy("a").get("extraKey") == "keep"


def test_add_remove_proxy():
    c = cfg.FrpcConfig()
    c.add_proxy({"name": "ssh", "type": "tcp", "localIP": "127.0.0.1", "localPort": 22, "remotePort": 6000})
    assert c.is_name_taken("ssh")
    assert not c.is_name_taken("web")
    assert c.remove_proxy("ssh")
    assert not c.is_name_taken("ssh")


def test_validate_common():
    c = cfg.FrpcConfig()
    assert c.validate_common()  # 空 server 应报错
    c.set_common(server_addr="h", server_port=7000, auth_token="")
    assert c.validate_common() == []


def test_load_missing_file_returns_empty():
    c = cfg.FrpcConfig.load("C:/nonexistent_path/frpc.toml")
    assert c.get_proxies() == []


def test_ensure_log_sets_login_fail_exit_false():
    """ensure_log 必须写入 loginFailExit=false，避免 frpc 连不上服务器就秒退。"""
    c = cfg.FrpcConfig()
    c.ensure_log(log_path="C:/data/frpC/frpc.log")
    assert c.data["loginFailExit"] is False
    assert c.data["log"]["to"] == "C:/data/frpC/frpc.log"
    # 不覆盖用户显式设置的 loginFailExit
    c2 = cfg.FrpcConfig({"loginFailExit": True})
    c2.ensure_log(log_path="x")
    assert c2.data["loginFailExit"] is True
