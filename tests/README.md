# 回归测试

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

测试使用模拟请求和临时配置，不需要 AutoDL 凭据，不会创建实例或发送邮件。

- `test_web_auth.py`：凭据类型、验证后保存、失败保留配置、退出登录及错误信息脱敏。
- `test_image_api.py`：镜像列表、UUID 解析、旧配置兼容和创建参数映射。
- `test_inventory_retry.py`：库存查询的有限重试、失败跳过本轮，以及鉴权和创建请求不重试。
