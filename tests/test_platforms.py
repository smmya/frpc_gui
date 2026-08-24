"""platforms.py 跨平台抽象层测试。"""
from pathlib import Path
from unittest import mock

import platforms as pf


def test_frpc_binary_name():
    expected = "frpc.exe" if pf.IS_WINDOWS else "frpc"
    assert pf.frpc_binary_name() == expected


def test_data_dir_exists_and_named():
    d = pf.data_dir()
    assert d.exists()
    assert d.name == "frpC"


def test_config_path_under_data_dir():
    cp = pf.config_path()
    assert cp.parent == pf.data_dir()
    assert cp.name == "frpc.toml"


def test_resource_dir_source_mode():
    # 源码运行（sys 无 _MEIPASS）时 resource_dir 指向 vendor/frpc
    assert not hasattr(pf.sys, "_MEIPASS")
    d = pf.resource_dir()
    assert d.name == "frpc"


def test_bundled_frpc_path_consistent():
    b = pf.bundled_frpc_path()
    if b is not None:
        assert b.name == pf.frpc_binary_name()
        assert b.exists()


def test_extract_frpc_roundtrip():
    bundled = pf.bundled_frpc_path()
    if bundled is None:
        return  # 未下载 vendor 二进制时跳过
    dest = pf.extract_frpc()
    assert dest is not None
    assert Path(dest).exists()
    assert Path(dest).name == pf.frpc_binary_name()
    # 再次提取应返回相同路径
    assert pf.extract_frpc() == dest


def test_systemd_unit_content():
    txt = pf._systemd_unit_content("/opt/frpC/frpc", "/home/u/.config/frpC/frpc.toml")
    assert "ExecStart=/opt/frpC/frpc -c /home/u/.config/frpC/frpc.toml" in txt
    assert "Restart=on-failure" in txt


def test_autostart_dir_name():
    assert pf._autostart_dir().name == "autostart"


def test_webview_backend_selection():
    if pf.IS_WINDOWS:
        assert pf.webview_gui_backend() == "edgechromium"
    elif pf.IS_LINUX:
        assert pf.webview_gui_backend() == "gtk"
    else:
        assert pf.webview_gui_backend() is None


def test_measure_tcp_latency_reachable():
    import socket
    import threading
    import time

    srv = socket.create_server(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    def serve():
        srv.listen(1)
        try:
            c, _ = srv.accept()
            c.close()
        finally:
            srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    time.sleep(0.1)
    ms = pf.measure_tcp_latency("127.0.0.1", port, timeout=1.0)
    assert ms is not None and ms >= 0
    t.join(timeout=2)


def test_measure_tcp_latency_unreachable():
    assert pf.measure_tcp_latency("127.0.0.1", 1, timeout=0.5) is None


def test_measure_tcp_latency_empty():
    assert pf.measure_tcp_latency("", 0) is None
