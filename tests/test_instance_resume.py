import os
import threading
import unittest
from unittest.mock import Mock, patch

import requests

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

from gpuhunter.autodl_client import AutodlClient, FailedError
from gpuhunter.instance_resume import ResumeAttempt, ResumeMonitor


def instance(**changes):
    return dict({
        "uuid": "test-original-instance", "machine_id": "test-original-host", "machine_alias": "测试机",
        "status": "shutdown", "charge_type": "payg", "req_gpu_amount": 1, "gpu_idle_num": 0,
        "start_mode": "gpu", "disk_health_status": "normal",
    }, **changes)


class ResumeAttemptTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(spec=AutodlClient)
        self.client.get_instance.return_value = instance()
        self.attempt = ResumeAttempt("test-original-instance")

    def test_waits_for_original_host_then_starts_only_original_uuid(self):
        self.assertEqual(self.attempt.step(self.client)[0], "waiting")
        self.client.power_on_instance.assert_not_called()
        self.client.get_instance.return_value = instance(gpu_idle_num=1)
        self.assertEqual(self.attempt.step(self.client)[0], "confirming")
        self.client.power_on_instance.assert_called_once_with("test-original-instance")
        self.client.create_instance.assert_not_called()

    def test_needs_all_gpus_requested_by_the_original_instance(self):
        self.client.get_instance.return_value = instance(gpu_idle_num=1, req_gpu_amount=2)
        self.assertEqual(self.attempt.step(self.client)[0], "waiting")
        self.client.power_on_instance.assert_not_called()

    def test_successful_submission_does_not_resubmit_while_shutdown_or_starting(self):
        self.client.get_instance.side_effect = [
            instance(gpu_idle_num=1), instance(gpu_idle_num=1), instance(status="starting"),
            instance(status="running"),
        ]
        states = [self.attempt.step(self.client, clock=lambda: 0)[0] for _ in range(4)]
        self.assertEqual(states, ["confirming", "confirming", "confirming", "done"])
        self.assertEqual(self.client.power_on_instance.call_count, 1)

    def test_network_or_database_uncertainty_only_reads_until_confirmed(self):
        for failure in (requests.Timeout(), requests.ConnectionError(),
                        FailedError("database", "DatabaseError"), FailedError("non JSON")):
            with self.subTest(failure=type(failure).__name__):
                attempt = ResumeAttempt("test-original-instance")
                self.client.reset_mock()
                self.client.get_instance.side_effect = None
                self.client.get_instance.return_value = instance(gpu_idle_num=1)
                self.client.power_on_instance.side_effect = failure
                self.assertEqual(attempt.step(self.client, clock=lambda: 0)[0], "confirming")
                self.assertEqual(attempt.step(self.client, clock=lambda: 1)[0], "confirming")
                with self.assertRaises(FailedError):
                    attempt.step(self.client, clock=lambda: 181)
                self.assertEqual(self.client.power_on_instance.call_count, 1)

    def test_explicit_capacity_rejection_allows_a_later_scan(self):
        self.client.get_instance.return_value = instance(gpu_idle_num=1)
        self.client.power_on_instance.side_effect = [FailedError("空闲 GPU 不足", "capacity-rejected"), None]
        self.assertEqual(self.attempt.step(self.client)[0], "waiting")
        self.assertEqual(self.attempt.step(self.client)[0], "confirming")
        self.assertEqual(self.client.power_on_instance.call_count, 2)

    def test_auth_account_and_unknown_rejections_stop(self):
        for code in ("AuthorizeFailed", "TORecharge", "TORealName", "UnknownRejection"):
            with self.subTest(code=code):
                attempt = ResumeAttempt("test-original-instance")
                self.client.get_instance.return_value = instance(gpu_idle_num=1)
                self.client.power_on_instance.side_effect = FailedError("操作被拒绝", code)
                with self.assertRaises(FailedError):
                    attempt.step(self.client)

    def test_already_running_completes_without_a_write(self):
        self.client.get_instance.return_value = instance(status="running")
        self.assertEqual(self.attempt.step(self.client)[0], "done")
        self.client.power_on_instance.assert_not_called()

    def test_external_start_is_observed_without_duplicate_write(self):
        self.client.get_instance.side_effect = [instance(status="starting"), instance(status="shutdown", gpu_idle_num=1)]
        for _ in range(2):
            self.assertEqual(self.attempt.step(self.client)[0], "confirming")
        self.client.power_on_instance.assert_not_called()

    def test_stops_on_released_wrong_instance_unsupported_or_broken_state(self):
        for changes in ({"status": "released"}, {"uuid": "different-instance"},
                        {"charge_type": "prepay"}, {"status": "shutdown_by_starting_error"},
                        {"disk_health_status": "abnormal"}, {"req_gpu_amount": None},
                        {"gpu_idle_num": "1"}, {"gpu_idle_num": True},
                        {"status": "running", "start_mode": "non_gpu"}):
            with self.subTest(changes=changes):
                self.client.get_instance.return_value = instance(**changes)
                with self.assertRaises(FailedError):
                    self.attempt.step(self.client)
        self.client.power_on_instance.assert_not_called()

    def test_host_or_gpu_change_stops_instead_of_following_other_resources(self):
        for changes in ({"machine_id": "another-host"}, {"req_gpu_amount": 2}):
            with self.subTest(changes=changes):
                attempt = ResumeAttempt("test-original-instance")
                self.client.get_instance.return_value = instance()
                attempt.step(self.client)
                self.client.get_instance.return_value = instance(gpu_idle_num=2, **changes)
                with self.assertRaises(FailedError):
                    attempt.step(self.client)
        self.client.power_on_instance.assert_not_called()

    def test_stop_during_status_query_prevents_power_on(self):
        cancelled = threading.Event()
        def read(_):
            cancelled.set()
            return instance(gpu_idle_num=1)
        self.client.get_instance.side_effect = read
        self.assertEqual(self.attempt.step(self.client, cancelled.is_set)[0], "stopped")
        self.client.power_on_instance.assert_not_called()


class ResumeMonitorTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("gpuhunter.instance_resume.logger")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = Mock(spec=AutodlClient)
        self.monitor = ResumeMonitor(client_factory=lambda: self.client)
        self.addCleanup(self.cleanup_monitor)

    def cleanup_monitor(self):
        self.monitor.stop()
        if self.monitor._thread is not None:
            self.monitor._thread.join(timeout=2)

    def test_fee_confirmation_and_input_validation_precede_worker(self):
        for args in [("id", 30, 120, False), ("", 30, 120, True), ("id", 1, 120, True),
                     ("id", 30, float('nan'), True), ("id", 30, 0, True)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.monitor.start(*args)
        self.client.get_instance.assert_not_called()

    def test_worker_rejects_duplicate_start_and_stop_interrupts_wait(self):
        queried = threading.Event()
        def read(_):
            queried.set()
            return instance()
        self.client.get_instance.side_effect = read
        self.monitor.start("test-original-instance", 600, 120, True)
        self.assertTrue(queried.wait(timeout=2))
        with self.assertRaises(ValueError):
            self.monitor.start("another-instance", 30, 120, True)
        self.monitor.stop()
        self.monitor._thread.join(timeout=2)
        self.assertFalse(self.monitor.snapshot()[0])
        self.client.power_on_instance.assert_not_called()

    def test_read_failure_retries_at_interval_and_retains_write_latch(self):
        self.client.get_instance.side_effect = [requests.Timeout(), instance(gpu_idle_num=1),
                                                requests.Timeout(), instance(status="running")]
        self.client.power_on_instance.side_effect = requests.Timeout()
        with patch.object(self.monitor._stop, "wait") as wait:
            self.monitor._run("test-original-instance", 30, 120)
        self.assertEqual([c.args[0] for c in wait.call_args_list], [30, 5, 30])
        self.client.power_on_instance.assert_called_once()
        self.assertIn("运行中", self.monitor.snapshot()[1])

    def test_deadline_reached_during_read_prevents_power_on(self):
        now = [0]
        def read(_):
            now[0] = 61
            return instance(gpu_idle_num=1)
        self.client.get_instance.side_effect = read
        with patch("gpuhunter.instance_resume.time.monotonic", side_effect=lambda: now[0]):
            self.monitor._run("test-original-instance", 30, 1)
        self.client.power_on_instance.assert_not_called()

    def test_login_expiry_stops_without_retries(self):
        self.client.get_instance.side_effect = FailedError("请重新登录", "AuthorizeFailed")
        self.monitor._run("test-original-instance", 30, 120)
        self.assertIn("重新登录", self.monitor.snapshot()[1])
        self.assertEqual(self.client.get_instance.call_count, 1)

    def test_confirmation_timeout_is_bounded_even_with_long_scan_interval(self):
        now = [0]
        self.client.get_instance.side_effect = [instance(gpu_idle_num=1), requests.Timeout()]
        def advance(seconds):
            now[0] += seconds
        with patch("gpuhunter.instance_resume.time.monotonic", side_effect=lambda: now[0]), \
                patch.object(self.monitor._stop, "wait", side_effect=advance) as wait:
            self.monitor._run("test-original-instance", 600, 120)
        self.assertEqual(now[0], 180)
        self.assertEqual([c.args[0] for c in wait.call_args_list], [5, 175])
        self.assertEqual(self.client.get_instance.call_count, 2)
        self.client.power_on_instance.assert_called_once()
        self.assertIn("尚未确认", self.monitor.snapshot()[1])


class ResumeClientTests(unittest.TestCase):
    def test_read_finds_exact_uuid_even_if_another_instance_has_idle_gpus(self):
        client = AutodlClient(token="test-only")
        target = instance()
        with patch.object(client, "list_instance", return_value=iter([
            instance(uuid="unrelated", gpu_idle_num=8), target,
        ])):
            self.assertEqual(client.get_instance(target["uuid"]), target)

    def test_missing_instance_stops(self):
        client = AutodlClient(token="test-only")
        with patch.object(client, "list_instance", return_value=iter([])), self.assertRaises(FailedError):
            client.get_instance("released-instance")

    def test_power_on_uses_exact_web_request_without_create_or_retry(self):
        client = AutodlClient(token="test-only")
        response = Mock(status_code=200)
        response.json.return_value = {"code": "Success"}
        with patch("requests.request", return_value=response) as request:
            client.power_on_instance("test-original-instance")
        request.assert_called_once_with(
            "POST", "https://www.autodl.com/api/v1/instance/power_on",
            json={"instance_uuid": "test-original-instance"},
            headers={"Authorization": "test-only", "Content-Type": "application/json"}, timeout=(5, 20),
        )
        with patch("requests.request", side_effect=requests.Timeout()) as request, self.assertRaises(requests.Timeout):
            client.power_on_instance("test-original-instance")
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
