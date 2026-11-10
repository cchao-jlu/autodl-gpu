# Windows 本地使用

- 双击 `start-local.cmd`，等待浏览器打开 http://127.0.0.1:7860 。服务在后台运行。
- 双击 `stop-local.cmd` 停止本地服务；关闭浏览器不会停止服务。
- 停止本地服务不会关闭已租用的 AutoDL 实例，实例请在 AutoDL 控制台管理。
- 首次打开后，填写普通实例的网页登录凭据，点击“验证并保存网页登录凭据”，选择显卡、地区和镜像，再开始蹲守。
- 点击“开始蹲守”后可能自动创建付费实例，请核对主机数量和定时关机选项。
- 本机“守到后将 Hunter 关机”保持“不关机”。

## 本地配置

Python 3.11，独立环境 `.venv`。仅监听 `127.0.0.1:7860`。
Windows 日志备份文件名已去除冒号；启动脚本开启 UTF-8 模式。
兼容依赖约束在 `requirements-local.txt`；安装版本快照在 `requirements-local.lock.txt`。
网页登录凭据保存在 `runtime/data/config.json` 的 `web_token` 字段，请勿分享。原开发者 Token 保留在 `token` 字段，不用于普通实例请求。运行数据已加入 Git 忽略规则。
后台启动日志在 `runtime/logs/web.stdout.log` 和 `runtime/logs/web.stderr.log`。

## 重建环境（已安装 uv 时）

在项目目录用 PowerShell 执行：

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements-local.lock.txt
.\start-local.cmd
```

## 获取普通实例的网页登录凭据

1. 在浏览器登录 https://www.autodl.com/console/instance/list 。
2. 按 F12 或 Ctrl+Shift+I，打开 Network（网络），选择 Fetch/XHR，刷新页面。
3. 选中 https://www.autodl.com/api/v1/instance 请求，打开 Headers（标头）。
4. 从 Request Headers（请求标头）中复制 Authorization 的值，粘贴到本地页面的密码输入框。
5. 也可以在 Application（应用）→ Local Storage（本地存储）→ https://www.autodl.com 下找到 `token` 并复制其 Value。

开发者 Token 与网页登录凭据不同，当前普通实例接口拒绝开发者 Token。
网页登录凭据过期后请重新登录官网并重新获取。不要把凭据发到聊天或截图中。
“验证并保存”只查询地区、GPU 和普通实例列表；全部成功后才会保存新凭据。
验证失败会保留原有配置。运行中发生鉴权错误会停止蹲守并显示重新登录提示。
网络请求失败不会自动重发创建请求，请先在官网确认实例状态再重试。

安装与页面可用不代表租用流程已经通过实测；只有用户点击“开始蹲守”后，才会按所选配置尝试创建付费实例。
