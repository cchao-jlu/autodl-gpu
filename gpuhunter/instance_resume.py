"""Wait for capacity on an existing instance's host, then start that instance once."""

import math
import threading
import time
from collections import deque
from datetime import datetime

import requests

from gpuhunter.autodl_client import FailedError, get_default_client
from main import logger


def instance_label(instance):
    return (f'{instance.get("region_name", "")} / {instance.get("machine_alias", "")} · '
            f'{instance.get("snapshot_gpu_alias_name", "GPU")} × {instance.get("req_gpu_amount", "?")} · '
            f'{instance["uuid"]}')


def is_capacity_error(error):
    # Only an explicit rejection for insufficient GPUs is safe to retry next round.
    message = "".join(str(error).lower().split())
    return any(text in message for text in (
        "空闲gpu不足", "gpu空闲不足", "gpu数量不足", "gpu资源不足", "无可用gpu",
    ))


def is_transient_read_error(error):
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(error, requests.HTTPError):
        return error.response is not None and error.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(error, FailedError) and error.code == "DatabaseError"


def positive_gpu_count(instance, key, allow_zero=False):
    value = instance.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < (0 if allow_zero else 1):
        raise FailedError("实例的 GPU 数量信息异常，已停止，请在官网确认。")
    return value


class ResumeAttempt:
    """One run's state machine. A submitted write is followed only by status reads."""

    def __init__(self, instance_uuid, confirm_timeout=180):
        self.instance_uuid = instance_uuid
        self.confirm_timeout = confirm_timeout
        self.confirm_until = None
        self.identity = None
        self.request_sent = False

    def step(self, client, cancelled=lambda: False, clock=None):
        clock = clock or time.monotonic
        if cancelled():
            return "stopped", "监控已停止。"
        if self.confirm_until is not None and clock() >= self.confirm_until:
            raise FailedError("开机结果尚未确认，已停止自动操作。请到官网检查实例状态后再决定是否重新监控。")
        instance = client.get_instance(self.instance_uuid)
        if cancelled():
            return "stopped", "监控已停止。"
        if instance.get("uuid") != self.instance_uuid:
            raise FailedError("接口返回的实例 ID 不匹配，已停止。")

        status = instance.get("status")
        if status == "running":
            if instance.get("start_mode") == "non_gpu":
                raise FailedError("该实例正在无卡模式运行，请先在官网处理。")
            return "done", "已确认原实例处于运行中，本次监控完成。"
        if self.confirm_until is not None:
            if status not in ("shutdown", "starting"):
                raise FailedError(f"开机后实例状态为 {status!r}，请到官网检查。")
            if clock() >= self.confirm_until:
                raise FailedError("开机结果尚未确认，已停止自动操作。请到官网检查实例状态后再决定是否重新监控。")
            return "confirming", f"正在确认开机结果（当前状态：{status}），不会重复提交开机请求。"
        if status == "starting":
            self.confirm_until = clock() + self.confirm_timeout
            return "confirming", "实例已在启动中，等待运行状态。"
        if status != "shutdown":
            raise FailedError(f"实例当前状态为 {status!r}，仅支持正常已关机的普通实例。")
        if instance.get("charge_type") != "payg":
            raise FailedError("当前仅支持按量计费的普通实例。")
        if instance.get("disk_health_status") == "abnormal":
            raise FailedError("实例磁盘状态异常，请先在官网处理。")

        required = positive_gpu_count(instance, "req_gpu_amount")
        idle = positive_gpu_count(instance, "gpu_idle_num", allow_zero=True)
        identity = (instance.get("machine_id"), required)
        if not identity[0]:
            raise FailedError("无法确认实例所在主机，已停止。")
        if self.identity is None:
            self.identity = identity
        elif identity != self.identity:
            raise FailedError("监控期间实例主机或 GPU 配置发生变化，请重新选择实例。")
        if idle < required:
            return "waiting", f"{instance.get('machine_alias', '原主机')} 空闲 GPU {idle} 张，需要 {required} 张，继续等待。"

        if cancelled():
            return "stopped", "监控已停止。"
        # Latch before I/O: a timeout or invalid response can still mean the write succeeded.
        self.request_sent = True
        self.confirm_until = clock() + self.confirm_timeout
        try:
            client.power_on_instance(self.instance_uuid)
        except FailedError as error:
            if error.code in ("AuthorizeFailed", "MissingWebToken", "TORecharge", "TORealName"):
                raise
            if is_capacity_error(error):
                self.confirm_until = None
                self.request_sent = False
                return "waiting", "开机被拒绝：GPU 空闲不足，等待下一轮扫描。"
            if error.code not in (None, "DatabaseError"):
                raise
            return "confirming", "开机接口返回异常，先查询实例状态确认结果，不重复提交。"
        except requests.RequestException:
            return "confirming", "开机请求未收到确定结果，先查询实例状态，不重复提交。"
        return "confirming", "开机请求已提交，等待确认实例进入运行状态。"


class ResumeMonitor:
    """One shared, cancellable background worker per Hunter process."""

    def __init__(self, client_factory=get_default_client):
        self.client_factory = client_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._active = False
        self._thread = None
        self._status = "尚未开始监控。"
        self._logs = deque(maxlen=200)

    def snapshot(self):
        with self._lock:
            return self._active, self._status, "\n".join(self._logs)

    def _record(self, message):
        with self._lock:
            self._status = message
            self._logs.append(f"[{datetime.now():%H:%M:%S}] {message}")
        logger.info("[蹲守开机] %s", message)

    def start(self, instance_uuid, interval_seconds, wait_minutes, confirmed=False):
        if confirmed is not True:
            raise ValueError("请确认自动开机后会产生实例运行费用。")
        if not isinstance(instance_uuid, str) or not instance_uuid.strip():
            raise ValueError("请先刷新并选择一个已关机实例。")
        try:
            interval = float(interval_seconds)
            duration = float(wait_minutes)
            if not (math.isfinite(interval) and 10 <= interval <= 600
                    and math.isfinite(duration) and 1 <= duration <= 1440):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("扫描间隔须为 10–600 秒，最长等待须为 1–1440 分钟。") from None
        with self._lock:
            if self._active:
                raise ValueError("已有开机监控任务，请先停止当前任务。")
            self._stop = threading.Event()
            self._logs.clear()
            self._status = "正在读取实例状态…"
            self._active = True
            self._thread = threading.Thread(
                target=self._run, args=(instance_uuid.strip(), interval, duration), daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._active = False
                raise

    def stop(self):
        with self._lock:
            if self._active:
                self._stop.set()
                self._status = "正在停止；已发出的开机请求仍可能完成，请以官网状态为准。"

    def _run(self, instance_uuid, interval, duration):
        deadline = time.monotonic() + duration * 60
        attempt = ResumeAttempt(instance_uuid)
        try:
            client = self.client_factory()
            self._record(f"监控原实例 {instance_uuid}；每 {interval:g} 秒扫描，最多等待 {duration:g} 分钟。")
            while not self._stop.is_set():
                if time.monotonic() >= deadline:
                    self._record("已到最长等待时间，监控结束。" + (
                        "开机请求已经发出，请在官网确认结果。" if attempt.request_sent else ""))
                    return
                try:
                    state, message = attempt.step(
                        client, cancelled=lambda: self._stop.is_set() or time.monotonic() >= deadline,
                    )
                except (FailedError, requests.RequestException) as error:
                    if not is_transient_read_error(error):
                        raise
                    # A read can fail after a write too; the pending latch must be retained.
                    if attempt.confirm_until is not None and time.monotonic() >= attempt.confirm_until:
                        raise FailedError("暂时无法确认开机结果，已停止。请在官网核查后再操作。") from None
                    state, message = "retry", "状态查询暂时失败，稍后重试。"
                if state == "stopped" and attempt.request_sent:
                    message += "开机请求已发出，请在官网确认结果；停止监控不会关机。"
                self._record(message)
                if state in ("done", "stopped"):
                    return
                delay = min(5 if state == "confirming" else interval, max(0, deadline - time.monotonic()))
                if attempt.confirm_until is not None:
                    delay = min(delay, max(0, attempt.confirm_until - time.monotonic()))
                self._stop.wait(delay)
            self._record("监控已停止。" + (
                "开机请求已发出，请在官网确认结果；停止监控不会关机。" if attempt.request_sent else ""))
        except FailedError as error:
            self._record(f"监控已停止：{error}")
        except requests.RequestException:
            self._record("网络请求失败，监控已停止，请检查网络及官网实例状态。")
        except Exception:
            # Do not include API payloads, credentials or connection details in UI logs.
            self._record("监控遇到异常，已停止自动操作，请在官网确认实例状态。")
        finally:
            with self._lock:
                self._active = False
