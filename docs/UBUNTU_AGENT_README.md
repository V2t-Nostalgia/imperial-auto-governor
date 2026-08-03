<!-- SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Ubuntu Agent 快速开始

`IAGUbuntuAgent` 是正式、独立的 Linux Release 附件，不需要再从源码包手工挑文件。以下步骤以全新 Ubuntu 24.04 LTS x86_64 为基线。

## 校验与解压

从同一 GitHub Release 下载：

```text
IAGUbuntuAgent-0.5.8-linux-x86_64.tar.gz
SHA256SUMS-0.5.8.txt
```

先核对顶层清单，再解压：

```bash
sha256sum --check --ignore-missing SHA256SUMS-0.5.8.txt
tar -xzf IAGUbuntuAgent-0.5.8-linux-x86_64.tar.gz
cd IAGUbuntuAgent-0.5.8
sha256sum --check SHA256SUMS.txt
```

包内清单覆盖 `agent_runtime/`、存档解析所需的 `save_state/`、Linux 回退路径依赖的 `packet_interceptor/`、全部文档和 `RELEASE_MANIFEST.json`。不要在校验前运行下载内容。

## 安装

安装脚本通过 Ubuntu 软件源安装 Python、编译依赖、`xdotool`、FFmpeg 与 NetfilterQueue 依赖，并在解压目录创建 `.venv`：

```bash
bash agent_runtime/install_ubuntu.sh
```

要求 Python 3.11 或更高版本。需要使用自备解释器或把虚拟环境放到其他位置时：

```bash
IAG_PYTHON=<PYTHON_EXECUTABLE> \
IAG_VENV_ROOT=<VENV_DIRECTORY> \
bash agent_runtime/install_ubuntu.sh
```

脚本不会向系统 Python 安装 pip 包。首次运行会从 `agent_runtime/agent_config.example.json` 生成包根目录下的 `agent_config.json`。

## 配置

编辑 `agent_config.json`，至少确认：

- `game_root` 指向本机 Stellaris 安装目录；
- `display` 与 `xauthority` 对应运行 Stellaris 的 X11 会话；
- `save_source_mode` 保持 `host_upload`；
- `host_ip` 可以留空自动发现，存在多个对等端时再填写 `<HOST_IP>`；
- `base_url`、`model` 与模型鉴权由前端或环境变量配置；
- `autonomy_mode` 默认是 `paused`，完成校准前不要改为执行模式。

API Key、上传令牌、证书、日志、数据库和存档只会生成在运行目录中，不属于发行包。

控制台中的“下载已配对 Windows 房主执行桥”以 Agent 包内经过发布校验的公开 Host Bridge ZIP 为基础，在通过前端登录认证后生成仅供当前 Agent 使用的临时配对包。该包包含当前访问地址、TLS 指纹和桥接令牌，不依赖服务器上遗留的临时下载文件，也不应作为公开附件再次分发。

## 前台启动

完成 HTTPS 证书和前端密码配置后，可使用：

```bash
bash agent_runtime/start_console.sh
```

另一台电脑访问：

```text
https://<AGENT_IP>:8765/
```

## systemd 服务

需要开机启动时，在安装和配置完成后执行：

```bash
IAG_LAN_IP=<AGENT_IP> bash agent_runtime/install_systemd_service.sh
```

该脚本安装控制台与只读网络观察器两个 unit。生产建设仍使用 Windows 房主桥的入站改写；Linux NFQUEUE 路径只保留为显式研究回退。

## 本地健康检查

```bash
.venv/bin/python -m compileall -q agent_runtime save_state
PYTHONPATH="agent_runtime:save_state" .venv/bin/python -c \
  "import extract_game_state, extract_planet_profiles, planner"
.venv/bin/python -m unittest discover -s agent_runtime/tests -p 'test_*.py' -v
.venv/bin/python -m unittest discover -s save_state -p 'test_*.py' -v
```

联网检索服务的可选部署与信任边界见 [RESEARCH_SERVICES.md](RESEARCH_SERVICES.md)。完整操作、载体校准与实局闭环见 `agent_runtime/README.md`。
