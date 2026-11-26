"""Budgeted prepay purchases with a durable journal before every write.

The API contract is recorded in docs/package-api.md. Money stays in AutoDL's
integer units (1 yuan = 1000 units); an ambiguous write is never retried.
"""

import hashlib
import json
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from gpuhunter.autodl_client import FailedError, get_default_client
from gpuhunter.instance_resume import is_transient_read_error
from main import DATA_DIR, logger

PACKAGE_TYPES = {"daily": ("包日", 6, 1), "weekly": ("包周", 3, 7), "monthly": ("包月", 12, 30)}
PAID = {"paid", "success"}
TERMINAL = {"complete", "closed", "capacity_rejected"}


def integer(value, minimum, maximum, label):
    if isinstance(value, bool):
        raise ValueError(f"{label}须为 {minimum}–{maximum} 的整数。")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.to_integral_value() or not minimum <= number <= maximum:
            raise ValueError
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}须为 {minimum}–{maximum} 的整数。") from None
    return int(number)


def money_units(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
            raise ValueError
        return int(amount * 1000)
    except (InvalidOperation, ValueError):
        raise ValueError("预算须为大于零的金额，最多两位小数。") from None


def api_amount(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FailedError("报价或订单金额格式异常，已停止支付。")
    return value


def yuan(value):
    return format(Decimal(value) / 1000, ".3f").rstrip("0").rstrip(".")


@dataclass(frozen=True)
class PackageSpec:
    charge_type: str
    duration: int
    region_signs: list
    gpu_types: list
    gpu_count: int
    instance_count: int
    image_info: dict
    max_instance_units: int
    max_total_units: int
    interval: int = 30
    wait_minutes: int = 120

    def validate(self):
        if self.charge_type not in PACKAGE_TYPES:
            raise ValueError("请选择包日、包周或包月。")
        integer(self.duration, 1, PACKAGE_TYPES[self.charge_type][1], "购买时长")
        integer(self.gpu_count, 1, 12, "每台 GPU 数量")
        integer(self.instance_count, 1, 20, "购买台数")
        integer(self.interval, 10, 600, "扫描间隔")
        integer(self.wait_minutes, 1, 1440, "最长等待分钟数")
        if not self.region_signs or not self.gpu_types:
            raise ValueError("请选择地区和显卡型号。")
        if not any(self.image_info.get(k) for k in ("base_image_uuid", "private_image_uuid", "reproduction_uuid")):
            raise ValueError("请选择有效镜像。")
        for value in (self.max_instance_units, self.max_total_units):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("请设置单台套餐总价上限和任务总预算。")
        return self

    def price_info(self, machine_id):
        return dict(machine_id=machine_id, charge_type=self.charge_type, duration=self.duration,
                    num=self.gpu_count, expand_data_disk=0, coupon_id_list=[])


def eligible(machine, spec, now=None):
    """Use per-machine capacity and the same 30-day month as the web form."""
    now = now or datetime.now(timezone.utc)
    try:
        deadline = datetime.fromisoformat(machine["rent_deadline"].replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            return False
        return (machine.get("pay_" + spec.charge_type) is True
                and machine.get("region_sign") in spec.region_signs
                and machine.get("gpu_name") in spec.gpu_types
                and machine.get("on_line") not in (3, 4, 5, 6)
                and machine["gpu_idle_num"] >= spec.gpu_count
                and machine["gpu_order_num"] >= spec.gpu_count
                and machine["max_instance_num"] > machine["binding_instance_num"]
                and now + timedelta(days=PACKAGE_TYPES[spec.charge_type][2] * spec.duration) < deadline)
    except (KeyError, ValueError, TypeError):
        return False


class Journal:
    def __init__(self, path=None):
        self.path = Path(path or Path(DATA_DIR) / "package-task.json")
        self.lock_file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.with_suffix(".lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise ValueError("另一个 Hunter 进程正在操作套餐任务，请先停止它。") from None
        self.lock_file = handle

    def release(self):
        if self.lock_file:
            self.lock_file.close()
            self.lock_file = None

    def load(self):
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("attempts"), list):
                raise ValueError
            return data
        except (ValueError, AttributeError):
            raise ValueError("套餐任务记录损坏，已禁止新购买；请先核对官网订单并保留本地记录。") from None

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)
        if os.name != "nt":
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def archive(self, data):
        # Archives contain no credentials; retain order evidence after completing a task.
        destination = self.path.with_name(f"package-task-{data['task_id']}.json")
        if self.path.exists():
            os.replace(self.path, destination)


class PackageTask:
    def __init__(self, spec, journal, data=None, clock=time.time):
        self.spec = spec.validate()
        self.journal = journal
        self.clock = clock
        self.data = data or {"version": 1, "task_id": uuid.uuid4().hex,
                             "spec": asdict(spec), "attempts": [], "finished": False}
        self.deadline = clock() + spec.wait_minutes * 60
        self.save()

    def save(self):
        self.journal.save(self.data)

    @property
    def spent(self):
        return sum(a.get("paid_units", 0) for a in self.data["attempts"])

    @property
    def completed(self):
        return sum(a["state"] == "complete" for a in self.data["attempts"])

    def allowed(self, amount):
        return amount <= self.spec.max_instance_units and self.spent + amount <= self.spec.max_total_units

    def validate_price(self, price, expected):
        if not isinstance(price, dict) or any(price.get(k) != expected[k] for k in
                ("machine_id", "charge_type", "duration", "num", "expand_data_disk")):
            raise FailedError("报价与所选主机、套餐、时长或 GPU 数量不一致，已停止。")
        return api_amount(price.get("deal_price"))

    def reconcile(self, client, attempt, cancelled, read_only=False):
        order_id = attempt.get("order_uuid")
        if not order_id:
            raise FailedError("创建订单的结果不确定，禁止重复提交。请在官网“我的订单”核对后结束此任务。")
        response = client.get_order(order_id)
        order = response.get("order") if isinstance(response, dict) else None
        expected = self.spec.price_info(attempt["machine_id"])
        if not isinstance(order, dict) or order.get("uuid") != order_id or order.get("order_type") != "create_instance":
            raise FailedError("订单身份或类型不匹配，已停止。")
        if order.get("machine_id") != attempt["machine_id"] or order.get("charge_type") != self.spec.charge_type:
            raise FailedError("订单主机或计费方式不匹配，已停止。")
        price = order.get("price_info", {})
        if any(price.get(k) != expected[k] for k in ("duration", "num", "expand_data_disk")):
            raise FailedError("订单的购买时长、GPU 数量或磁盘配置不匹配，已停止。")
        runtime = order.get("runtime_info", {})
        for key in ("base_image_uuid", "private_image_uuid", "reproduction_uuid", "reproduction_id"):
            if self.spec.image_info.get(key) and runtime.get(key) != self.spec.image_info[key]:
                raise FailedError("订单镜像与任务不匹配，已停止。")
        amount = api_amount(order.get("deal_price"))
        status = order.get("status")
        if status in PAID:
            attempt["paid_units"] = amount
            attempt["state"] = "paid"
            instance_id = order.get("product_uuid")
            if not isinstance(instance_id, str) or not instance_id:
                self.save()
                raise FailedError("订单已支付但缺少实例 ID，请到官网核对；不会再次购买。")
            if attempt.get("instance_uuid") and attempt["instance_uuid"] != instance_id:
                self.save()
                raise FailedError("支付返回的实例与订单实例不匹配，请到官网核对。")
            attempt["instance_uuid"] = instance_id
            self.save()
            try:
                instance = client.get_instance(instance_id)
            except FailedError as error:
                if error.code == "InstanceNotFound" and self.clock() - attempt.get("confirm_started", attempt["created_at"]) <= 180:
                    return "订单已支付，等待新实例出现在实例列表中。"
                raise
            if (instance.get("uuid") != instance_id or instance.get("machine_id") != attempt["machine_id"]
                    or instance.get("charge_type") != self.spec.charge_type
                    or instance.get("req_gpu_amount") != self.spec.gpu_count):
                raise FailedError("已支付实例与套餐配置不匹配，请到官网核对。")
            if instance.get("status") == "running":
                attempt["state"] = "complete"
                self.save()
                return f"套餐实例已运行：{instance_id}；订单 ¥{yuan(amount)}。"
            if instance.get("status") not in ("creating", "starting", "re_initializing"):
                raise FailedError("套餐已支付，但实例未运行，请在官网处理；不会再次购买。")
            if self.clock() - attempt.get("confirm_started", attempt["created_at"]) > 180:
                raise FailedError("订单已支付，等待实例运行超时；请到官网核对，不会再次购买。")
            return "订单已支付，等待实例进入运行状态。"
        if status in ("closed", "timeout"):
            if attempt.get("paid_units"):
                raise FailedError("已支付订单状态发生变化，请到官网核对。")
            attempt["state"] = "closed"
            self.save()
            return "订单已关闭且未支付，将继续查询库存。"
        if status != "unpaid":
            raise FailedError(f"订单状态为 {status!r}，已暂停，请在官网核对。")
        if attempt["state"] in ("payment_submitting", "paid"):
            if self.clock() - attempt.get("confirm_started", attempt["created_at"]) > 180:
                raise FailedError("支付结果仍未确认，已暂停；请到官网核对，不会重复支付或购买。")
            return "支付结果尚未确认，只查询订单，不重复支付。"
        if not self.allowed(amount):
            raise FailedError("订单金额超过预算，未支付。请在官网取消此待支付订单后再继续。")
        if read_only or cancelled() or self.clock() >= self.deadline:
            return "订单待支付；本次只核对状态，未付款。"
        attempt.update(state="payment_submitting", reserved_units=amount, confirm_started=self.clock())
        self.save()  # Persist before I/O, including process crashes immediately after this line.
        try:
            result = client.pay_package_order(order_id)
            if isinstance(result, str) and result:
                attempt["instance_uuid"] = result
                self.save()
        except (FailedError, requests.RequestException):
            # Even a server/network failure may have committed payment. Only read next.
            return "支付请求未得到确定结果，转为查询订单确认。"
        return "已提交支付，等待订单与实例状态确认。"

    def step(self, client, cancelled=lambda: False, read_only=False):
        pending = next((a for a in self.data["attempts"] if a["state"] not in TERMINAL), None)
        if pending:
            message = self.reconcile(client, pending, cancelled, read_only)
            if self.completed >= self.spec.instance_count:
                self.data["finished"] = True
                self.save()
                return True, f"套餐抢购完成：{self.completed} 台，订单合计 ¥{yuan(self.spent)}。{message}"
            return False, message
        if self.completed >= self.spec.instance_count:
            self.data["finished"] = True
            self.save()
            return True, f"套餐抢购完成：{self.completed} 台，订单合计 ¥{yuan(self.spent)}。"
        if read_only:
            return True, f"已核对：{self.completed} 台运行，订单合计 ¥{yuan(self.spent)}；没有待确认订单。"
        if cancelled():
            return True, "已停止套餐抢购。"
        if self.clock() >= self.deadline:
            raise FailedError("已达到最长等待时间；保留任务进度，可手动继续。")
        if self.spent >= self.spec.max_total_units:
            raise FailedError("任务预算已用完，已停止。")
        machines = client.list_machine(self.spec.region_signs, self.spec.gpu_types, self.spec.gpu_count,
                                       charge_type=self.spec.charge_type)
        checked = 0
        for machine in machines:
            if cancelled() or self.clock() >= self.deadline:
                return True, "已停止或达到等待时限，未提交新订单。"
            if not eligible(machine, self.spec):
                continue
            checked += 1
            expected = self.spec.price_info(machine["machine_id"])
            amount = self.validate_price(client.preview_package_price(expected), expected)
            if not self.allowed(amount):
                if checked >= 20:
                    break
                continue
            if cancelled() or self.clock() >= self.deadline:
                return True, "已停止或达到等待时限，未提交新订单。"
            name = "hunter-" + self.data["task_id"][:12] + "-" + str(len(self.data["attempts"]) + 1)
            attempt = dict(state="order_submitting", machine_id=machine["machine_id"], instance_name=name,
                           quoted_units=amount, created_at=self.clock())
            self.data["attempts"].append(attempt)
            self.save()
            info = {**self.spec.image_info, "machine_id": machine["machine_id"],
                    "charge_type": self.spec.charge_type, "req_gpu_amount": self.spec.gpu_count,
                    "instance_name": name, "expand_data_disk": 0}
            try:
                result = client.create_package_order(info, expected)
            except FailedError as error:
                # Observed web code: this is a definite failure to reserve capacity.
                if error.code == "MachineGpuNumUpdateFailed":
                    attempt["state"] = "capacity_rejected"
                    self.save()
                    return False, "GPU 库存已被抢走，等待下一轮。"
                raise FailedError("订单创建失败或结果不确定，已保留记录并停止，请核对官网订单。") from None
            except requests.RequestException:
                raise FailedError("创建订单的请求结果不确定，已保留记录；请核对官网订单，禁止重复提交。") from None
            if not isinstance(result, dict) or not isinstance(result.get("uuid"), str) or not result["uuid"]:
                raise FailedError("创建订单返回异常，已停止；请在官网核对，不会重新提交。")
            attempt.update(order_uuid=result["uuid"], state="order_created")
            self.save()
            return False, f"已创建待支付订单 {result['uuid']}，下一步核对订单金额。"
        return False, "暂无满足套餐、时长、GPU 数量和预算的空闲主机，继续等待。"


class PackageMonitor:
    def __init__(self, journal=None, client_factory=get_default_client):
        self.journal = journal or Journal()
        self.client_factory = client_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._active = False
        self._logs = deque(maxlen=150)
        self._status = "尚未开始套餐抢购。服务重启后需手动继续并核对已有订单。"

    def snapshot(self):
        with self._lock:
            return self._active, self._status, "\n".join(self._logs)

    def record(self, message):
        with self._lock:
            self._status = message
            self._logs.append(f"[{datetime.now():%H:%M:%S}] {message}")
        logger.info("[套餐抢购] %s", message)

    def start(self, spec=None, confirmed=False, read_only=False):
        if not read_only and confirmed is not True:
            raise ValueError("请先确认套餐、购买数量和预算，并勾选自动购买确认。")
        with self._lock:
            if self._active:
                raise ValueError("已有套餐任务正在运行，请先停止。")
            self.journal.acquire()
            try:
                data = self.journal.load()
                if read_only:
                    if not data:
                        raise ValueError("没有可核对的套餐任务。")
                    spec = PackageSpec(**data["spec"])
                else:
                    spec.validate()
                    if data and data.get("finished"):
                        self.journal.archive(data)
                        data = None
                    if data and data["spec"] != asdict(spec):
                        raise ValueError("存在未结束的套餐任务。请用原配置继续，或先核对并结束旧任务再修改配置。")
                client = self.client_factory()
                if not client.token:
                    raise ValueError("请先验证网页登录凭据。")
                credential = hashlib.sha256(client.token.encode()).hexdigest()
                if data and data.get("credential") != credential:
                    raise ValueError("登录凭据已变化。请先在官网核对旧任务订单，再手动结束旧任务。")
                task = PackageTask(spec, self.journal, data)
                task.data["credential"] = credential
                task.save()
                self._active = True
                self._stop = threading.Event()
                self._logs.clear()
                thread = threading.Thread(target=self._run, args=(task, client, read_only), daemon=True)
                thread.start()
            except Exception:
                self._active = False
                self.journal.release()
                raise

    def _run(self, task, client, read_only):
        try:
            self.record("正在核对已有订单。" if read_only else "套餐任务已开始；先核对已有订单，再查询套餐库存。")
            while not self._stop.is_set():
                try:
                    done, message = task.step(client, self._stop.is_set, read_only)
                    self.record(message)
                    if done or read_only:
                        break
                except (FailedError, requests.RequestException) as error:
                    if is_transient_read_error(error) and time.time() < task.deadline:
                        self.record("状态或库存读取暂时失败，稍后重试读取。")
                    else:
                        if isinstance(error, FailedError):
                            self.record(str(error))
                        else:
                            self.record("网络读取失败，已暂停，请核对订单后继续。")
                        break
                if time.time() >= task.deadline:
                    self.record("已达到最长等待时间，保留订单及进度；请手动核对后继续。")
                    break
                pending = any(a["state"] not in TERMINAL for a in task.data["attempts"])
                if self._stop.wait(2 if pending else task.spec.interval):
                    break
            if self._stop.is_set():
                self.record("已停止后续操作。已提交请求可能仍会完成，请核对订单。")
        except Exception:
            self.record("任务异常，已保留订单记录并停止。请核对官网订单后处理。")
        finally:
            with self._lock:
                self._active = False
                self.journal.release()

    def stop(self):
        self._stop.set()

    def finish_manually(self, confirmed=False):
        if confirmed is not True:
            raise ValueError("请先在官网核对所有订单、取消待支付订单，并勾选结束任务确认。")
        with self._lock:
            if self._active:
                raise ValueError("请先停止套餐任务，等待当前请求结束。")
            self.journal.acquire()
            try:
                data = self.journal.load()
                if data:
                    data["manually_finished"] = True
                    self.journal.save(data)
                    self.journal.archive(data)
            finally:
                self.journal.release()
        self.record("旧任务已归档。新任务会重新计算购买台数和预算。")
