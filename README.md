# frpC · frpc 图形化管理工具

基于 [fatedier/frp](https://github.com/fatedier/frp) 的 `frpc` 客户端，提供跨平台（Windows / Linux）图形化界面：

- 设置页：系统服务、开机自启开关，并实时检查状态；
- 主界面：显示 frp 版本、连接状态、运行状态、当前规则；
- 新建/编辑规则弹窗：提交前自动校验（参数非法 / 名称重复 / 端口·域名冲突等）。

## 架构

- 前端：`pywebview` + 原生 HTML/CSS/JS（双主题）。Windows 用 WebView2，Linux 用 WebKit2GTK。
- 后端：Python 标准库（`config` 数据模型、`validation` 校验、`controller` 门面、`status` Admin API、`api_bridge` JS 桥接）。
- 平台抽象：`platforms.py` 统一处理数据目录、frpc 二进制定位/释出、服务管理、开机自启、进程管理。

## 关键设计：frpc 内嵌

**不再依赖用户手动放置 frpc 二进制**。构建时通过 `scripts/fetch_frpc.py` 下载固定版本 frpc，
由 PyInstaller 打入单文件；运行时 `platforms.extract_frpc()` 自动释出到本程序的数据目录。

| 平台 | 数据目录（配置 + frpc 二进制） |
| --- | --- |
| Windows | `%APPDATA%\frpC\` |
| Linux | `~/.config/frpC/`（遵循 `$XDG_CONFIG_HOME`） |

## 本地构建

```bash
# 1. 准备构建环境（Python 3.12+）
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install pywebview==6.2.1 pyinstaller==6.22.2 pytest
# Windows 额外：pip install pythonnet==3.1.0 clr_loader==0.3.1
# Linux 额外：   pip install pygobject（需系统 libwebkit2gtk-4.0 + gir1.2-webkit2-4.0）

# 2. 下载 frpc 二进制（固定 v0.70.1）
python scripts/fetch_frpc.py            # 自动按当前平台
# 或指定：python scripts/fetch_frpc.py --platform linux

# 3. 打包
python -m PyInstaller frpC.spec --noconfirm --clean
# 产物：dist/frpC(.exe)
```

## GitHub Actions 自动构建与发布

`.github/workflows/build.yml` 在 `windows-latest` 与 `ubuntu-22.04` 上构建：

- 推送到任意分支 / PR：构建并上传 artifact；
- 推送 `v*` tag 或手动触发（workflow_dispatch）：构建并**自动发布 Release**（含 Windows `.exe` 与 Linux 二进制）。

## frp 版本

固定为 **v0.70.1**（`scripts/fetch_frpc.py` 顶部 `FRP_VERSION`）。注意 v0.68.x 及以下受
CVE-2026-40910 影响，请勿降级到 0.68 以下。
