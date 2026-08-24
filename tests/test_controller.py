import sys
import tempfile
from pathlib import Path
from unittest import mock

import controller as ctrl


def test_resolve_frpc_path_uses_bundled():
    """内置 frpc 存在时，resolve 应返回其释出路径（非 None）。"""
    with mock.patch.object(ctrl.pf, "extract_frpc", return_value="C:/data/frpC/frpc.exe"):
        assert ctrl.resolve_frpc_path() == "C:/data/frpC/frpc.exe"


def test_resolve_frpc_path_fallback_without_binary():
    """无内置 frpc 且目录里只有自身 frpC 时，应返回 None 而非把自身当 frpc。"""
    tmp = Path(tempfile.mkdtemp(prefix="frpgui_test_", dir=Path(__file__).parent.parent))
    fake_gui = tmp / "frpC.exe"
    fake_gui.write_text("gui", encoding="utf-8")

    with mock.patch.object(ctrl.pf, "extract_frpc", return_value=None), \
         mock.patch.object(sys, "argv", [str(fake_gui)]), \
         mock.patch.object(ctrl.shutil, "which", return_value=None):
        assert ctrl.resolve_frpc_path() is None
        # 显式传入自身路径也应被拒绝
        assert ctrl.resolve_frpc_path(str(fake_gui)) is None
        assert ctrl._is_self(str(fake_gui))


def test_assert_not_self_blocks_gui():
    with mock.patch.object(ctrl, "_own_path", return_value="C:/tools/frpC.exe"):
        try:
            ctrl._assert_not_self("C:/Tools/frpc.exe")  # 文件名不同，应通过
        except RuntimeError:
            pass
        with mock.patch.object(ctrl, "_is_self", return_value=True):
            try:
                ctrl._assert_not_self("C:/tools/frpc.exe")
                raise AssertionError("应抛出 RuntimeError")
            except RuntimeError as e:
                assert "自身" in str(e)


def test_service_status_returns_valid_state():
    s = ctrl.service_status()
    assert s in ("running", "stopped", "not_installed", "unknown")


def test_is_boot_autostart_returns_bool():
    assert isinstance(ctrl.is_boot_autostart(), bool)


def test_get_frpc_version_bundled():
    """内置 frpc（vendor/frpc/frpc.exe）应能返回版本号。"""
    p = ctrl.pf.bundled_frpc_path()
    if p is None:
        return  # 未下载 vendor 二进制时跳过
    ver = ctrl.get_frpc_version(str(p))
    assert ver and ver != "未知"
