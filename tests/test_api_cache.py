"""api_bridge 子进程探测缓存回归测试（防「轮询拉起进程」回归）。

验证 get_state() 在短时间内多次调用时，boot / service / version / process
四类探测都命中本地缓存，不会每次轮询都拉起 reg.exe / sc.exe / tasklist / frpc。
"""
from pathlib import Path
import tempfile

import api_bridge as api


def test_get_state_caches_subprocess_probes(monkeypatch):
    calls = {"boot": 0, "service": 0, "process": 0, "version": 0}

    def bump(key, ret):
        calls[key] += 1
        return ret

    monkeypatch.setattr(api.ctrl, "is_boot_autostart", lambda: bump("boot", False))
    monkeypatch.setattr(api.ctrl, "service_status", lambda: bump("service", "not_installed"))
    monkeypatch.setattr(api.ctrl, "process_running", lambda p: bump("process", False))
    monkeypatch.setattr(api.ctrl, "get_frpc_version", lambda p: bump("version", "0.70.1"))
    # 日志解析不涉及子进程，桩替换为固定结果以隔离真实日志文件
    monkeypatch.setattr(api.logstatus, "parse",
                        lambda p: {"login_ok": None, "login_msg": "", "proxies": {}})

    # 用不自动删除的临时目录（避免沙箱 safe-delete 在 teardown 阶段拦截）
    d = Path(tempfile.mkdtemp(prefix="api_", dir=Path(__file__).parent.parent))
    obj = api.FrpApi(d / "frpc.toml", "C:/dummy/frpc.exe")
    obj.get_state()
    obj.get_state()  # 立即第二次，应全部命中缓存

    assert calls["boot"] == 1, f"boot 探测未缓存：{calls}"
    assert calls["service"] == 1, f"service 探测未缓存：{calls}"
    assert calls["version"] == 1, f"version 探测未缓存：{calls}"
    assert calls["process"] == 1, f"process 探测未缓存：{calls}"
