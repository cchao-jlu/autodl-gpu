import unittest
from unittest.mock import Mock, patch

import requests

from gpuhunter.autodl_client import AutodlClient, FailedError, TransientReadError, autodl_client
from gpuhunter.commands.wait import try_to_create_instances
from gpuhunter.data_object import Config


def response(code="Success", data=None, status=200):
    result = Mock(status_code=status)
    result.json.return_value = {"code": code, "data": data, "msg": "服务异常，请稍后重试"}
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(response=result)
    return result


class InventoryRetryTests(unittest.TestCase):
    def setUp(self):
        self.client = AutodlClient(token="test-only")
        sleep_patch = patch("gpuhunter.autodl_client.time.sleep")
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        for name in ("gpuhunter.autodl_client.logger", "gpuhunter.commands.wait.logger"):
            logger_patch = patch(name)
            logger_patch.start()
            self.addCleanup(logger_patch.stop)

    def test_database_error_recovers_on_next_read(self):
        inventory = [{"RTX 4090": {"idle_gpu_num": 1, "total_gpu_num": 3}}]
        with patch("requests.request", side_effect=[response("DatabaseError"), response(data=inventory)]) as request:
            self.assertEqual(self.client.get_region_gpu_types(["beijing-C"]), inventory)
        self.assertEqual(request.call_count, 2)
        self.sleep.assert_called_once_with(1)
        self.assertTrue(all(call.args[1].endswith("/machine/region/gpu_type") for call in request.call_args_list))

    def test_exhaustion_is_bounded_and_marks_read_failure(self):
        with patch("requests.request", return_value=response("DatabaseError")) as request:
            with self.assertRaises(TransientReadError) as error:
                self.client.get_region_gpu_types(["beijing-C"])
        self.assertEqual(request.call_count, 3)
        self.assertEqual(error.exception.code, "DatabaseError")
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [1, 2])

    def test_failed_inventory_round_does_not_reach_creation(self):
        with patch("gpuhunter.data_object.Config.load", return_value=Config()), \
                patch("gpuhunter.data_object.RegionList.update", side_effect=TransientReadError("temporary", "DatabaseError")), \
                patch.object(autodl_client, "create_instance") as create, \
                patch("gpuhunter.autodl_client.resolve_image_info") as resolve:
            self.assertFalse(try_to_create_instances())
        create.assert_not_called()
        resolve.assert_not_called()

    def test_authentication_errors_are_not_retried(self):
        with patch("requests.request", return_value=response("AuthorizeFailed")) as request:
            with self.assertRaises(FailedError) as error:
                self.client.get_region_gpu_types(["beijing-C"])
        self.assertNotIsInstance(error.exception, TransientReadError)
        self.assertEqual(request.call_count, 1)
        self.sleep.assert_not_called()

    def test_creation_is_never_retried_on_database_error(self):
        with patch("requests.request", return_value=response("DatabaseError")) as request:
            with self.assertRaises(FailedError):
                self.client.create_instance("test-machine", base_image_uuid="test-image")
        self.assertEqual(request.call_count, 1)
        self.sleep.assert_not_called()

    def test_transient_http_and_timeout_can_recover(self):
        with patch("requests.request", side_effect=[requests.Timeout(), response(status=503), response(data=[])]):
            self.assertEqual(self.client.get_region_gpu_types(["beijing-C"]), [])
        self.assertEqual(self.sleep.call_count, 2)

    def test_nontransient_http_error_is_not_retried(self):
        with patch("requests.request", return_value=response(status=400)) as request:
            with self.assertRaises(requests.HTTPError):
                self.client.get_region_gpu_types(["beijing-C"])
        self.assertEqual(request.call_count, 1)
        self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
