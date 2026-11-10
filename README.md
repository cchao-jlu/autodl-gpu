# AutoDL GPU Hunter

面向 **AutoDL 普通容器实例**的 GPU 蹲守工具。通过本地 Gradio 页面选择显卡、地区和镜像，定时查询库存，并在满足条件时尝试创建按量付费实例。

本仓库在上游项目基础上修复了普通实例登录、镜像接口和 Windows 本地运行问题。**适配普通实例；未实现 Pro 实例流程。**

> 点击“验证并保存网页登录凭据”只查询资源；点击“开始蹲守”后可能自动创建付费实例。关闭浏览器或停止本地工具，不会关闭已创建的云端实例。

[快速开始](#快速开始) · [登录配置](#登录配置) · [使用方法](#使用方法) · [常见问题](#常见问题) · [提交问题](https://github.com/cchao-jlu/autodl-gpu/issues)

## 功能与修复

| 项目 | 说明 |
| --- | --- |
| GPU 蹲守 | 按显卡型号、地区、每台 GPU 数量及目标主机数量筛选，定时查询库存 |
| 普通实例登录 | 使用官网网页登录凭据；验证成功后保存，鉴权失败时给出明确提示 |
| 镜像选择 | 适配基础镜像、社区镜像和我的镜像的列表接口 |
| 基础镜像标识 | 使用镜像 UUID 解析与构造创建参数，避免旧树形镜像接口失效 |
| Windows 启动 | 独立虚拟环境、后台启动/停止脚本、UTF-8 日志及合法备份文件名 |
| 实例选项 | 数据盘扩容、克隆实例、定时关机和邮件通知 |
| 本地访问 | Web 服务默认仅监听 `127.0.0.1`，不创建 Gradio 公共分享链接 |

## 快速开始

### 环境

- 推荐 **Python 3.11**，本地安装不需要 GPU 或 CUDA。
- Git，以及能够访问 GitHub、Python 包源和 AutoDL 的网络。
- Windows 本地启动流程已验证。Linux/macOS 可按下方方式直接运行 Python，尚未在本仓库中实机验证。

### Windows / PowerShell

已安装 Python 3.11 时：

```powershell
git clone https://github.com/cchao-jlu/autodl-gpu.git
cd autodl-gpu
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-local.lock.txt
.\start-local.cmd
```

使用 `uv` 时，可用以下命令替代创建环境和安装依赖的步骤：

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements-local.lock.txt
.\start-local.cmd
```

启动成功后打开 **http://127.0.0.1:7860**。之后可直接双击：

| 文件 | 用途 |
| --- | --- |
| `start-local.cmd` | 启动后台服务并打开浏览器；已运行时复用该服务 |
| `stop-local.cmd` | 停止由脚本启动的本地服务 |
| `local-server.ps1` | 检查端口、管理进程并等待服务就绪 |

需要在终端查看输出时，也可以直接运行：

```powershell
$env:PYTHONUTF8 = '1'
$env:GRADIO_ANALYTICS_ENABLED = 'False'
.\.venv\Scripts\python.exe app.py
```

直接运行的进程使用 `Ctrl+C` 停止。不要与后台启动脚本同时运行多个蹲守进程。

### Linux / macOS

```bash
git clone https://github.com/cchao-jlu/autodl-gpu.git
cd autodl-gpu
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-local.txt
PYTHONUTF8=1 GRADIO_ANALYTICS_ENABLED=False .venv/bin/python app.py
```

`requirements-local.lock.txt` 保存 Windows / Python 3.11 已验证环境的完整版本；`requirements-local.txt` 提供兼容约束。请勿仅安装原始 `requirements.txt`，以免旧版 Gradio 与新版间接依赖发生冲突。

## 登录配置

### 使用网页登录凭据

**当前普通实例接口使用网页登录凭据，开发者 Token 无法替代它。**

1. 在浏览器登录 [AutoDL 控制台](https://www.autodl.com/console/instance/list)。
2. 按 **F12** 或 **Ctrl+Shift+I** 打开开发者工具，选择 **Network / 网络 → Fetch/XHR**，刷新页面。
3. 选中请求地址为 `https://www.autodl.com/api/v1/instance` 的请求。
4. 打开 **Headers / 标头 → Request Headers / 请求标头**，复制 **Authorization 的完整值**。
5. 粘贴到本地页面的“网页登录凭据”，点击 **验证并保存网页登录凭据**。

![在浏览器 Network 面板找到 Authorization，并粘贴到本地工具](docs/screenshots/guide-auth.png)

*凭据获取操作示意；图中使用占位符。*

也可以在 **Application / 应用 → Local Storage / 本地存储 → https://www.autodl.com** 下找到 `token`，复制其 Value。只复制值，不要包含字段名、引号或整段请求。

验证过程会查询地区、GPU 和普通实例列表。全部成功后才保存新凭据；验证失败时保留原配置。凭据过期后，需要重新登录官网并再次复制，刷新本地页面不会自动更新凭据。

### 凭据保存位置

配置以明文保存在本机 `runtime/data/config.json`：

| 字段 | 用途 |
| --- | --- |
| `web_token` | 普通实例的网页登录凭据 |
| `token` | 从旧版本保留的开发者 Token，不参与当前普通实例请求 |
| `mail_smtp_password` | 可选的邮箱发信密码或授权码 |

不要公开配置文件、请求标头或包含凭据的截图。页面“退出”只清除本地的 `web_token`，不会注销官网会话，也不会清除旧 Token 和邮件设置。`runtime/` 中的运行数据与日志已加入 Git 忽略规则。

## 使用方法

![配置显卡、地区、GPU 数量、镜像和定时关机](docs/screenshots/guide-overview.png)

*当前版本界面，使用演示数据。图中的库存和配置仅用于说明操作。*

1. **选择 GPU 和地区**：勾选显卡型号与地区，指定每台 GPU 数量以及目标主机数量。
2. **选择启动镜像**：基础镜像会显示框架、Python、CUDA/运行时及硬件架构。请按目标 GPU 和软件要求选择兼容镜像。
3. **检查实例选项**：按需设置磁盘扩容、克隆、定时关机和邮件通知。本机运行时，“守到后将 Hunter 关机”保持“不关机”。
4. **开始蹲守**：默认间隔为 30 秒。点击按钮后，程序会保存设置、查询符合条件的主机并尝试创建实例。
5. **查看与停止**：页面显示蹲守日志；点击“停止”可停止后续扫描。已发出的请求可能仍在处理，请在 AutoDL 控制台确认实例状态。

![基础镜像选项中的框架、Python、系统、CUDA 与硬件架构说明](docs/screenshots/guide-images.png)

*镜像选择示例：核对框架、Python、CUDA 与硬件架构后，再开始蹲守。*

网络请求失败时，程序不会自动重发创建请求。遇到异常请先检查官网实例列表，再决定是否重新开始，避免重复租用。

## 常见问题

| 问题 | 处理方式 |
| --- | --- |
| 开发者 Token 正确，仍提示登录失败 | 请使用官网请求头中的网页登录凭据，见[登录配置](#登录配置) |
| 登录凭据过期 | 重新登录官网，重新获取 Authorization，再在本地验证 |
| 基础镜像出现红色“错误” | 更新代码和兼容依赖，重启服务并按 `Ctrl+F5` 刷新；当前版本已替换旧 `/image/all` 接口 |
| `HfFolder` 导入失败，或首页出现 Gradio/FastAPI 异常 | 在独立环境中按本仓库锁定文件重新安装依赖 |
| 7860 端口被占用 | 使用 `stop-local.cmd` 停止本工具的后台服务；若被其他程序占用，先处理该程序 |
| 网页打开了，但不出现新功能 | 重启本地服务并强制刷新浏览器 |
| 显卡空闲数量为 0 | 当前查询没有空闲库存；工具只能持续查询，无法保证抢到资源 |
| 关闭浏览器后仍在运行 | 后台服务独立运行，使用 `stop-local.cmd` 停止；云端实例需在 AutoDL 控制台单独管理 |

后台启动日志：

- `runtime/logs/web.stdout.log`：服务标准输出。
- `runtime/logs/web.stderr.log`：启动错误和异常堆栈。
- `runtime/logs/output.log`：页面使用的蹲守日志。
- `runtime/logs/main.log`：详细调试日志，提交问题前先脱敏。

更多 Windows 操作说明见 [LOCAL-SETUP.md](LOCAL-SETUP.md)。

## 上游与许可证

基于 [ansatzia/autodl-gpuhunter](https://github.com/ansatzia/autodl-gpuhunter) 整理与修复，原项目作者为 [mfznttkx](https://github.com/mfznttkx/autodl-gpuhunter)。保留上游提交历史和版权声明。

采用 [MIT License](LICENSE)。工具本身开源，AutoDL 云资源按平台规则收费。本项目为第三方工具，非 AutoDL 官方产品。
