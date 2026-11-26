import copy
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from gpuhunter.autodl_client import AutodlClient, FailedError
from gpuhunter.package_hunt import (Journal, PackageMonitor, PackageSpec, PackageTask,
                                   api_amount, eligible, integer, money_units)


NOW = 1800000000


def spec(mode="daily", **kwargs):
    return replace(PackageSpec(mode, 1, ["test-region"], ["RTX 4090"], 2, 1,
                               {"base_image_uuid": "test-image"}, 100000, 200000), **kwargs)


def machine(**kwargs):
    result = dict(machine_id="test-machine", region_sign="test-region", gpu_name="RTX 4090",
                  pay_daily=True, pay_weekly=True, pay_monthly=True,
                  gpu_idle_num=2, gpu_order_num=2, max_instance_num=10, binding_instance_num=1,
                  on_line=2, rent_deadline="2099-01-01T00:00:00+08:00")
    return {**result, **kwargs}


class PackageTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = Journal(Path(self.temp.name) / "task.json")
        self.now = NOW
        self.settings = spec()
        self.task = PackageTask(self.settings, self.journal, clock=lambda: self.now)
        self.client = Mock(token="test-credential")
        self.client.list_machine.return_value = [machine()]
        self.client.preview_package_price.side_effect = lambda expected: {**expected, "deal_price": 90000}
        self.client.create_package_order.return_value = {"uuid": "test-order"}
        self.client.pay_package_order.return_value = "test-instance"
        self.order = dict(uuid="test-order", order_type="create_instance", machine_id="test-machine",
                          charge_type="daily", price_info=self.settings.price_info("test-machine"),
                          runtime_info=self.settings.image_info, deal_price=90000, status="unpaid",
                          product_uuid="test-instance")
        self.client.get_order.side_effect = lambda _: {"order": copy.deepcopy(self.order)}
        self.client.get_instance.return_value = dict(uuid="test-instance", status="running",
                                                     machine_id="test-machine", charge_type="daily", req_gpu_amount=2)

    def create(self):
        self.task.step(self.client)
        self.client.create_package_order.assert_called_once()

    def pay(self):
        self.create()
        self.task.step(self.client)
        self.client.pay_package_order.assert_called_once_with("test-order")

    def test_complete_purchase_requires_paid_order_and_running_instance(self):
        self.pay()
        self.order["status"] = "paid"
        self.task.step(self.client)
        done, message = self.task.step(self.client)
        self.assertTrue(done)
        self.assertEqual(self.task.completed, 1)
        self.assertEqual(self.task.spent, 90000)
        self.assertIn("90", message)
        self.client.pay_package_order.assert_called_once()
        self.client.create_package_order.assert_called_once()
        self.assertTrue(self.journal.load()["finished"])

    def test_all_three_modes_use_selected_duration_and_gpu_count(self):
        for mode, duration in [("daily", 6), ("weekly", 3), ("monthly", 12)]:
            with self.subTest(mode=mode):
                settings = spec(mode, duration=duration)
                task = PackageTask(settings, self.journal)
                self.client.reset_mock()
                task.step(self.client)
                args = self.client.create_package_order.call_args.args
                self.assertEqual(args[0]["charge_type"], mode)
                self.assertEqual(args[1]["duration"], duration)
                self.assertEqual(args[1]["num"], 2)
                self.assertEqual(args[1]["expand_data_disk"], 0)
                self.assertEqual(self.client.list_machine.call_args.kwargs["charge_type"], mode)

    def test_over_budget_preview_never_creates_or_pays(self):
        self.client.preview_package_price.side_effect = lambda p: {**p, "deal_price": 100001}
        self.task.step(self.client)
        self.client.create_package_order.assert_not_called()
        self.client.pay_package_order.assert_not_called()

    def test_order_price_increase_is_checked_again_before_payment(self):
        self.create()
        self.order["deal_price"] = 100001
        with self.assertRaisesRegex(FailedError, "超过预算"):
            self.task.step(self.client)
        self.client.pay_package_order.assert_not_called()
        self.assertEqual(self.journal.load()["attempts"][0]["state"], "order_created")

    def test_cumulative_budget_counts_previous_successful_purchases(self):
        settings = spec(instance_count=2, max_total_units=150000)
        self.task = PackageTask(settings, self.journal, clock=lambda: self.now)
        self.pay()
        self.order["status"] = "paid"
        self.task.step(self.client)
        self.task.step(self.client)
        self.client.create_package_order.assert_called_once()
        self.assertEqual(self.task.completed, 1)

    def test_creation_timeout_is_durable_and_never_retried_after_restart(self):
        self.client.create_package_order.side_effect = requests.Timeout()
        with self.assertRaisesRegex(FailedError, "结果不确定"):
            self.task.step(self.client)
        restored = PackageTask(self.settings, self.journal, self.journal.load(), clock=lambda: self.now)
        with self.assertRaisesRegex(FailedError, "禁止重复提交"):
            restored.step(self.client)
        self.client.create_package_order.assert_called_once()
        self.client.pay_package_order.assert_not_called()

    def test_payment_timeout_can_reconcile_success_without_repayment_after_restart(self):
        self.client.pay_package_order.side_effect = requests.Timeout()
        self.pay()
        restored = PackageTask(self.settings, self.journal, self.journal.load(), clock=lambda: self.now)
        restored.step(self.client)
        self.client.pay_package_order.assert_called_once()
        self.order["status"] = "paid"
        restored.step(self.client)
        self.assertTrue(restored.step(self.client)[0])
        self.client.pay_package_order.assert_called_once()
        self.client.create_package_order.assert_called_once()

    def test_ambiguous_payment_remains_blocked_after_confirmation_timeout(self):
        self.pay()
        self.now += 181
        with self.assertRaisesRegex(FailedError, "支付结果仍未确认"):
            self.task.step(self.client)
        self.client.pay_package_order.assert_called_once()
        self.client.create_package_order.assert_called_once()

    def test_journal_is_saved_before_each_write(self):
        def create(*_):
            self.assertEqual(self.journal.load()["attempts"][0]["state"], "order_submitting")
            return {"uuid": "test-order"}
        def pay(*_):
            self.assertEqual(self.journal.load()["attempts"][0]["state"], "payment_submitting")
            return "test-instance"
        self.client.create_package_order.side_effect = create
        self.client.pay_package_order.side_effect = pay
        self.pay()

    def test_disk_failure_prevents_submission(self):
        with patch.object(self.journal, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.task.step(self.client)
        self.client.create_package_order.assert_not_called()

    def test_disk_failure_prevents_payment(self):
        self.create()
        with patch.object(self.journal, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.task.step(self.client)
        self.client.pay_package_order.assert_not_called()

    def test_explicit_capacity_rejection_allows_next_round(self):
        self.client.create_package_order.side_effect = FailedError("sold", "MachineGpuNumUpdateFailed")
        self.task.step(self.client)
        self.client.create_package_order.side_effect = None
        self.task.step(self.client)
        self.assertEqual(self.client.create_package_order.call_count, 2)
        self.client.pay_package_order.assert_not_called()

    def test_unknown_creation_failure_does_not_retry(self):
        self.client.create_package_order.side_effect = FailedError("unknown", "DatabaseError")
        with self.assertRaises(FailedError):
            self.task.step(self.client)
        with self.assertRaises(FailedError):
            self.task.step(self.client)
        self.client.create_package_order.assert_called_once()

    def test_malformed_create_response_does_not_retry(self):
        self.client.create_package_order.return_value = None
        with self.assertRaises(FailedError):
            self.task.step(self.client)
        with self.assertRaises(FailedError):
            self.task.step(self.client)
        self.client.create_package_order.assert_called_once()

    def test_stop_between_quote_and_create_blocks_order(self):
        stopped = False
        def quote(p):
            nonlocal stopped
            stopped = True
            return {**p, "deal_price": 90000}
        self.client.preview_package_price.side_effect = quote
        self.task.step(self.client, lambda: stopped)
        self.client.create_package_order.assert_not_called()

    def test_stop_between_order_read_and_payment_blocks_payment(self):
        self.create()
        self.task.step(self.client, lambda: True)
        self.client.pay_package_order.assert_not_called()

    def test_deadline_before_payment_blocks_payment(self):
        self.create()
        self.now += 121 * 60
        self.task.step(self.client)
        self.client.pay_package_order.assert_not_called()

    def test_read_only_reconciliation_cannot_create_or_pay(self):
        self.task.step(self.client, read_only=True)
        self.client.create_package_order.assert_not_called()
        self.create()
        self.task.step(self.client, read_only=True)
        self.client.pay_package_order.assert_not_called()

    def test_order_identity_and_configuration_must_match(self):
        self.create()
        original = copy.deepcopy(self.order)
        for changes in [dict(uuid="other"), dict(order_type="renewal_instance"), dict(machine_id="other"),
                        dict(charge_type="weekly"), dict(price_info={**original["price_info"], "num": 8}),
                        dict(runtime_info={"base_image_uuid": "other"}), dict(deal_price="90000")]:
            self.order = {**original, **changes}
            with self.assertRaises(FailedError):
                self.task.step(self.client)
        self.client.pay_package_order.assert_not_called()

    def test_mismatched_quote_is_rejected(self):
        self.client.preview_package_price.side_effect = lambda p: {**p, "num": 8, "deal_price": 90000}
        with self.assertRaises(FailedError):
            self.task.step(self.client)
        self.client.create_package_order.assert_not_called()

    def test_unpaid_expired_order_is_not_paid(self):
        self.create()
        self.order["status"] = "timeout"
        self.task.step(self.client)
        self.assertEqual(self.journal.load()["attempts"][0]["state"], "closed")
        self.client.pay_package_order.assert_not_called()

    def test_paid_instance_failure_never_triggers_replacement(self):
        self.pay()
        self.order["status"] = "paid"
        self.client.get_instance.return_value["status"] = "shutdown"
        with self.assertRaises(FailedError):
            self.task.step(self.client)
        self.client.create_package_order.assert_called_once()
        self.assertEqual(self.task.spent, 90000)

    def test_instance_starting_does_not_count_as_complete(self):
        self.pay()
        self.order["status"] = "paid"
        self.client.get_instance.return_value["status"] = "starting"
        self.task.step(self.client)
        self.assertEqual(self.task.completed, 0)
        self.assertEqual(self.task.spent, 90000)
        self.client.create_package_order.assert_called_once()

    def test_unpaid_order_is_resumed_without_creating_another(self):
        self.create()
        restored = PackageTask(self.settings, self.journal, self.journal.load(), clock=lambda: self.now)
        restored.step(self.client)
        self.client.create_package_order.assert_called_once()
        self.client.pay_package_order.assert_called_once_with("test-order")

    def test_paid_order_waits_for_instance_to_appear(self):
        self.pay()
        self.order["status"] = "paid"
        self.client.get_instance.side_effect = FailedError("not visible yet", "InstanceNotFound")
        done, message = self.task.step(self.client)
        self.assertFalse(done)
        self.assertIn("等待", message)
        self.assertEqual(self.task.spent, 90000)
        self.client.create_package_order.assert_called_once()
        self.client.pay_package_order.assert_called_once()

    def test_no_matching_capacity_never_quotes(self):
        self.client.list_machine.return_value = [machine(gpu_order_num=1), machine(pay_daily=False)]
        self.task.step(self.client)
        self.client.preview_package_price.assert_not_called()


class PackageValidationTests(unittest.TestCase):
    def test_duration_ranges_and_numeric_validation(self):
        for mode, max_duration in [("daily", 6), ("weekly", 3), ("monthly", 12)]:
            spec(mode, duration=max_duration).validate()
            with self.assertRaises(ValueError):
                spec(mode, duration=max_duration + 1).validate()
        for value in [float("nan"), float("inf"), True, 1.5, 0, -1, "abc"]:
            with self.assertRaises(ValueError):
                integer(value, 1, 12, "GPU")
        for value in ["NaN", "Infinity", "-1", "0", "1.234", True]:
            with self.assertRaises(ValueError):
                money_units(value)
        self.assertEqual(money_units("46.55"), 46550)
        for value in [1.5, True, "20", None, -1]:
            with self.assertRaises(FailedError):
                api_amount(value)

    def test_eligibility_includes_term_capacity_region_and_deadline(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(eligible(machine(), spec(), now))
        for changes in [dict(pay_daily=False), dict(gpu_idle_num=1), dict(gpu_order_num=1),
                        dict(binding_instance_num=10), dict(on_line=3), dict(region_sign="elsewhere"),
                        dict(gpu_name="different"), dict(rent_deadline="unknown"),
                        dict(rent_deadline=(now + timedelta(hours=23)).isoformat())]:
            self.assertFalse(eligible(machine(**changes), spec(), now), changes)
        self.assertFalse(eligible(machine(rent_deadline=(now + timedelta(days=29)).isoformat()), spec("monthly"), now))

    def test_journal_lock_excludes_second_worker_and_survives_corrupt_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.json"
            first, second = Journal(path), Journal(path)
            first.acquire()
            try:
                with self.assertRaises(ValueError):
                    second.acquire()
            finally:
                first.release()
            second.acquire()
            second.release()
            path.write_text("broken")
            with self.assertRaises(ValueError):
                first.load()

    def test_monitor_requires_confirmation_and_matching_unfinished_task(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "task.json")
            monitor = PackageMonitor(journal, lambda: Mock(token="test"))
            with self.assertRaises(ValueError):
                monitor.start(spec())
            PackageTask(spec(), journal)
            with self.assertRaisesRegex(ValueError, "原配置"):
                monitor.start(spec("monthly"), confirmed=True)
            with self.assertRaisesRegex(ValueError, "凭据已变化"):
                monitor.start(spec(), confirmed=True)
            with self.assertRaises(ValueError):
                monitor.finish_manually(False)
            monitor.finish_manually(True)
            self.assertFalse(journal.path.exists())
            self.assertTrue(list(Path(directory).glob("package-task-*.json")))

    def test_stop_monitor_interrupts_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "task.json")
            client = Mock(token="test")
            client.list_machine.return_value = []
            monitor = PackageMonitor(journal, lambda: client)
            with patch("gpuhunter.package_hunt.logger"):
                monitor.start(spec(), confirmed=True)
                monitor.stop()
                import time
                for _ in range(100):
                    if not monitor.snapshot()[0]:
                        break
                    time.sleep(.01)
                self.assertFalse(monitor.snapshot()[0])
            client.create_package_order.assert_not_called()
            client.pay_package_order.assert_not_called()
            text = journal.path.read_text()
            self.assertNotIn('"token"', text)
            self.assertNotIn('"web_token"', text)


class PackageApiTests(unittest.TestCase):
    def test_verified_endpoints_methods_and_payloads(self):
        client = AutodlClient(token="test")
        price = spec("monthly").price_info("machine")
        info = {"machine_id": "machine", "charge_type": "monthly"}
        with patch.object(client, "request", return_value={}) as request:
            client.preview_package_price(price)
            request.assert_called_with("/api/v1/order/price/preview", body=price)
            client.create_package_order(info, price)
            request.assert_called_with("/api/v1/order/instance/create/prepay", body={"instance_info": info, "price_info": price})
            client.get_order("order")
            request.assert_called_with("/api/v1/order", params={"order_uuid": "order"}, method="GET")
            client.pay_package_order("order")
            request.assert_called_with("/api/v1/order/pay", body={"order_uuid": "order"}, method="PUT")

    def test_writes_are_never_automatically_retried(self):
        client = AutodlClient(token="test")
        for operation in [lambda: client.create_package_order({}, {}), lambda: client.pay_package_order("order")]:
            with patch("requests.request", side_effect=requests.Timeout()) as request:
                with self.assertRaises(requests.Timeout):
                    operation()
                request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
