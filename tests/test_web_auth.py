import os
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

import app
from gpuhunter.autodl_client import AutodlClient, FailedError
from gpuhunter.data_object import Config
from requests import Timeout


def response(code="Success", data=None, message=""):
    result = Mock(status_code=200)
    result.json.return_value = {"code": code, "data": data, "msg": message}
    return result


class WebAuthTests(unittest.TestCase):
    def setUp(self):
        logger_patch = patch("gpuhunter.autodl_client.logger")
        logger_patch.start()
        self.addCleanup(logger_patch.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_patch = patch("gpuhunter.data_object.DATA_DIR", self.directory.name)
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        self.client_patch = patch.object(app.autodl_client, "token", "")
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        Config(token="old-developer-token", web_token="", instance_num=2).save()

    def test_developer_token_is_not_used_for_ordinary_instances(self):
        client = AutodlClient()
        client.load_config()
        with patch("requests.request") as request:
            with self.assertRaises(FailedError) as error:
                client.get_regions()
        self.assertEqual(error.exception.code, "MissingWebToken")
        request.assert_not_called()

    def test_same_developer_token_is_rejected_without_network(self):
        with patch("requests.request") as request:
            result = app.save_token(" old-developer-token ")
        request.assert_not_called()
        self.assertFalse(result[app.gr_is_token_ready].value)
        self.assertIn("开发者 Token", result[app.gr_token_input_error].value)

    def test_success_validates_only_reads_and_preserves_existing_settings(self):
        responses = [
            response("OK", [{"region_name": "测试区", "region_sign": ["test-A"]}]),
            response(data=[{"RTX 4090": {"idle_gpu_num": 1, "total_gpu_num": 3}}]),
            response(data={"list": [], "max_page": 0}),
        ]
        with patch("requests.request", side_effect=responses) as request:
            result = app.save_token("  website-session-secret  ")
        self.assertTrue(result[app.gr_is_token_ready].value)
        self.assertEqual(result[app.gr_token_input].value, "")
        config = Config().load()
        self.assertEqual(config.web_token, "website-session-secret")
        self.assertEqual(config.token, "old-developer-token")
        self.assertEqual(config.instance_num, 2)
        urls = [call.args[1] for call in request.call_args_list]
        self.assertEqual(urls, [
            "https://fe-config-backend.autodl.com/api/v1/autodl/region/tag",
            "https://www.autodl.com/api/v1/machine/region/gpu_type",
            "https://www.autodl.com/api/v1/instance",
        ])
        for call in request.call_args_list:
            self.assertEqual(call.kwargs["headers"]["Authorization"], "website-session-secret")
            self.assertEqual(call.kwargs["timeout"], (5, 20))

    def test_rejected_replacement_preserves_existing_session_and_hides_controls(self):
        Config(token="old-developer-token", web_token="previous-session").save()
        with patch("requests.request", return_value=response("AuthorizeFailed", message="bad-session")):
            result = app.save_token("bad-session")
        self.assertEqual(Config().load().web_token, "previous-session")
        self.assertFalse(result[app.gr_is_token_ready].value)
        self.assertFalse(result[app.gr_config_tab].visible)
        self.assertEqual(result[app.gr_token_input].value, "")
        self.assertNotIn("bad-session", result[app.gr_token_input_error].value)

    def test_network_timeout_does_not_retry_or_save(self):
        with patch("requests.request", side_effect=Timeout()) as request:
            result = app.save_token("new-session")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(Config().load().web_token, "")
        self.assertIn("网络", result[app.gr_token_input_error].value)

    def test_logout_clears_only_web_credential(self):
        Config(token="old-developer-token", web_token="session", instance_num=2).save()
        result = app.clear_token()
        config = Config().load()
        self.assertEqual(config.web_token, "")
        self.assertEqual(config.token, "old-developer-token")
        self.assertEqual(config.instance_num, 2)
        self.assertFalse(result[app.gr_config_tab].visible)

    def test_server_error_cannot_echo_credential(self):
        client = AutodlClient(token="private-session")
        with patch("requests.request", return_value=response("Error", message="Invalid private-session")):
            with self.assertRaises(FailedError) as error:
                client.get_regions()
        self.assertNotIn("private-session", str(error.exception))


if __name__ == "__main__":
    unittest.main()
