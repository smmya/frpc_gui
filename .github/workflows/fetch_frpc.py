#!/usr/bin/env python3
"""下载并释出 frpc 二进制到 vendor/frpc，供本地/CI 打包使用。

用法：
    python scripts/fetch_frpc.py [--platform windows|linux|auto] [--version 0.70.1]

说明：
- 从 fatedier/frp 官方 GitHub Release 下载对应平台的 frpc 二进制；
- Windows 解压 .zip，Linux 解压 .tar.gz，提取其中的 frpc(.exe)；
- 输出到 vendor/frpc/frpc(.exe) 并写入 vendor/frpc/frpc.version 版本标记；
- 仅依赖标准库（urllib/zipfile/tarfile），跨平台可运行。
"""
from __future__ import annotations

import argparse
import platform as _platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# 兜底：Windows 默认代码页（本地 GBK / CI 的 cp1252）无法编码中文，print 中文会抛
# UnicodeEncodeError。统一把标准输出/错误重配为 UTF-8，确保任何环境下中文可正常输出。
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream.encoding and _stream.encoding.lower() not in ("utf-8", "utf8"):
            _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

FRP_VERSION = "0.70.1"
BASE_URL = f"https://github.com/fatedier/frp/releases/download/v{FRP_VERSION}"

# 每种平台对应的 Release 资产、压缩包内 frpc 相对路径、输出二进制名
PLATFORMS = {
    "windows": {
        "asset": f"frp_{FRP_VERSION}_windows_amd64.zip",
        "member": f"frp_{FRP_VERSION}_windows_amd64/frpc.exe",
        "bin": "frpc.exe",
    },
    "linux": {
        "asset": f"frp_{FRP_VERSION}_linux_amd64.tar.gz",
        "member": f"frp_{FRP_VERSION}_linux_amd64/frpc",
        "bin": "frpc",
    },
}

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "frpc"


def detect_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    raise SystemExit(f"不支持的平台：{sys.platform}（当前仅支持 windows / linux）")


def download(url: str, dest: Path) -> None:
    print(f"[fetch] 下载 {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "frpC-build/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as f:
        shutil.copyfileobj(resp, f)


def extract(archive: Path, member: str, out_bin: Path) -> None:
    archive = Path(archive)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            data = zf.read(member)
    else:
        with tarfile.open(archive) as tf:
            data = tf.extractfile(member).read()
    out_bin.parent.mkdir(parents=True, exist_ok=True)
    out_bin.write_bytes(data)
    if not out_bin.name.endswith(".exe"):
        out_bin.chmod(out_bin.stat().st_mode | 0o111)


def main() -> None:
    ap = argparse.ArgumentParser(description="下载并释出 frpc 二进制")
    ap.add_argument("--platform", choices=["windows", "linux", "auto"], default="auto")
    ap.add_argument("--version", default=FRP_VERSION)
    args = ap.parse_args()

    target = detect_platform() if args.platform == "auto" else args.platform
    spec = PLATFORMS[target]
    asset = spec["asset"].replace(FRP_VERSION, args.version)

    with tempfile.TemporaryDirectory(prefix="frpc_fetch_") as tmp:
        archive = Path(tmp) / asset.split("/")[-1]
        download(f"{BASE_URL.replace(FRP_VERSION, args.version)}/{asset}", archive)
        out_bin = VENDOR_DIR / spec["bin"]
        extract(archive, spec["member"].replace(FRP_VERSION, args.version), out_bin)
        (VENDOR_DIR / "frpc.version").write_text(args.version + "\n", encoding="utf-8")
    print(f"[fetch] 完成：{out_bin}（frp v{args.version}）")


if __name__ == "__main__":
    main()
