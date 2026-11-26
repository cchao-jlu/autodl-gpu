# 普通实例包时段购买接口记录

核对日期：2026-11-26。来源是 AutoDL 官网当日公开前端脚本，以及已有网页登录会话下的**只读**库存与报价查询。本次没有创建真实订单、付款或启动云端实例。

## 核实的协议

| 操作 | 方法与路径 | 要点 |
| --- | --- | --- |
| 主机库存 | `POST /api/v1/user/machine/list` | `charge_type` 使用 `daily`、`weekly`、`monthly`，复用分页、地区、GPU 筛选 |
| 报价预览 | `POST /api/v1/order/price/preview` | `machine_id, charge_type, duration, num, expand_data_disk, coupon_id_list` |
| 创建待支付订单 | `POST /api/v1/order/instance/create/prepay` | `instance_info` 与 `price_info`；成功 `data.uuid` 为订单 ID，并非实例 ID |
| 查询订单 | `GET /api/v1/order?order_uuid=…` | `data.order` 包含 `uuid, order_type, machine_id, charge_type, price_info, runtime_info, deal_price, status, product_uuid` |
| 支付 | `PUT /api/v1/order/pay` | 仅提交 `order_uuid`；创建实例订单成功返回实例 ID |
| 查询实例 | `POST /api/v1/instance` | 复用原客户端，按实例 ID 核对主机、GPU 数量、套餐和运行状态 |

官网对应脚本：

- `https://www.autodl.com/assets/index-e5850bc7.js`：请求方法与路径。
- `https://www.autodl.com/assets/index-ba90943d.js`：新建实例参数、套餐时长和主机筛选；`MachineGpuNumUpdateFailed` 为明确的库存不足拒绝。
- `https://www.autodl.com/assets/getPrice-5016c51f.js`：报价接口。
- `https://www.autodl.com/assets/index-fb32dfac.js`：待支付订单详情、付款与实例结果查询。
- `https://www.autodl.com/assets/order-3fd97b35.js`：订单状态映射。

这些文件名会随官网发布变化，不能当作永久接口文档。写入部分通过官网代码核对及模拟测试验证，尚未完成真实付费端到端验收；协议变化时遇到未知字段或状态会停止，不能凭猜测继续支付。

## 时长与金额

- `daily`：1–6 天；`weekly`：1–3 周；`monthly`：1–12 个月。
- `duration` 表示所选单位的个数，不能将周换成天传入。官网用每月 30 天判断是否超过主机 `rent_deadline`，本地筛选沿用此逻辑；最终有效期以服务端订单为准。
- 必须同时满足主机的 `pay_daily/pay_weekly/pay_monthly`、`gpu_idle_num`、`gpu_order_num`、实例数容量和租用截止时间。地区库存只是展示信息，不是下单依据。
- 报价与订单 `deal_price` 的单位为 **1/1000 元**。例如只读报价返回 `46550`，代表 ¥46.55。预算用 `Decimal` 转为整数，整个购买过程不使用浮点金额比较。
- 预览价格用于筛选；支付前重新读取具体订单的 `deal_price`、配置和镜像，检查单台上限与任务剩余预算。预算限制的是本次订单金额，不是账户生命周期的全部费用。
- 只读核对发现：扩容数据盘费用在 `data_disk_info.data_disk_deal_daily_price` 中，`prepay_expand_data_disk=false`，且不包含在套餐 `deal_price` 中。因此第一期套餐固定 `expand_data_disk=0`。套餐不使用优惠券，不克隆，不修改到期续费/转按量设置，也不沿用按量实例的定时关机。

## 持久化与恢复

`runtime/data/package-task.json` 在每次创建订单、付款前原子写入并同步到磁盘；记录任务配置、尝试阶段、订单/实例 ID 和已付款金额，不保存 Authorization。凭据的 SHA-256 摘要用于阻止换登录身份后静默继续旧任务。

同一个运行数据目录通过操作系统文件锁限制为一个套餐工作进程。锁在进程退出后自动释放；不要复制运行目录并在不同目录重复启动同一购买任务。

- 创建结果不确定且没有订单 ID：阻止继续下单，要求在官网人工核对。
- 已知待支付订单且尚未提交支付：继续前重新查订单和预算，可支付原订单，不重新创建。
- 支付已提交但响应超时、格式异常或报错：只查订单，不重发付款；最多等待 180 秒确认，然后暂停。手动继续仍先读旧订单。
- 已付费但实例尚未运行：记录支出，只等待原实例；不买替代实例。
- 只有创建接口明确拒绝库存不足，或已知订单进入未支付的关闭/超时状态，才允许下一次库存尝试。
- 停止、超时、关闭浏览器和服务重启不会清空已购数量或待确认订单。服务重启后不会自行开始购买。
- “核对已有订单（不购买）”仅做一次状态核对。若凭据变化，需先在官网核对并结束旧任务。
- “归档并结束旧任务”需要明确勾选人工核对确认，保留旧记录为 `package-task-<任务ID>.json`；随后启动的是新任务，台数和预算重新计算。

## 验证

`tests/test_package_hunt.py` 覆盖三种套餐、报价与预算、订单配置校验、等待截止时间、停止、创建/支付不确定、持久化恢复、文件锁与模拟成功流程。原按量及蹲守开机测试仍需一起通过。

离线 UI 演示可用 `.venv/bin/python -m tests.demo_package_ui` 启动在 `127.0.0.1:7861`；使用临时配置和模拟 API，不读取真实凭据，不购买真实实例。README 的套餐截图由此演示生成。
