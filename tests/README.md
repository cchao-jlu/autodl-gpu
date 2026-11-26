# 回归测试

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

测试使用模拟请求和临时配置，不需要 AutoDL 凭据，不会创建实例或发送邮件。

- `test_web_auth.py`：凭据类型、验证后保存、失败保留配置、退出登录及错误信息脱敏。
- `test_image_api.py`：镜像列表、UUID 解析、旧配置兼容和创建参数映射。
- `test_inventory_retry.py`：库存查询的有限重试、失败跳过本轮，以及鉴权和创建请求不重试。
- `test_instance_resume.py`：原实例和主机匹配、GPU 数量门槛、开机结果确认、请求不确定时禁止重复提交、资源不足后继续等待、等待时限、停止和重复启动保护。开机接口全部使用模拟请求，不会实际开机或计费。

- `test_package_hunt.py`：包日/包周/包月的套餐筛选、整数金额、单台及累计预算、订单核对、超时不重试、重启恢复、停止和文件锁。所有创建/支付均使用模拟请求。

离线界面演示（临时配置，不会访问或购买真实 AutoDL 资源）：

```bash
.venv/bin/python -m tests.demo_package_ui
```

打开 `http://127.0.0.1:7861`，可操作完整的模拟套餐购买流程。按 `Ctrl+C` 停止。
