import json
import os
import unittest
from unittest.mock import patch

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

import app
from gpuhunter.autodl_client import (
    AutodlClient, FailedError, autodl_client, get_running_instances, resolve_image_info,
)


IMAGE = {
    "uuid": "base-image-test", "framework_name": "PyTorch", "framework_version": "2.8.0",
    "python_version": "3.12(ubuntu22.04)", "cuda_version": "12.8", "chip_corp": "nvidia",
    "cpu_arch": "x86", "is_off": False,
}


class ImageApiTests(unittest.TestCase):
    def test_flat_catalog_selection_resolves_to_create_request_uuid(self):
        with patch.object(autodl_client, "get_base_images", return_value=[IMAGE]):
            result = app.load_image_options("base")
            label, value = result[app.gr_base_image].choices[0]
            self.assertIn("nvidia", label)
            self.assertTrue(result[app.gr_base_image_group].visible)
            self.assertFalse(result[app.gr_shared_image_group].visible)
            info = resolve_image_info(**json.loads(value))
        client = AutodlClient(token="test-only")
        with patch.object(client, "request", return_value="test-instance") as request:
            client.create_instance("test-machine", req_gpu_amount=2, **info)
        payload = request.call_args.kwargs["body"]["instance_info"]
        self.assertEqual(payload["base_image_uuid"], IMAGE["uuid"])
        self.assertEqual(payload["req_gpu_amount"], 2)
        self.assertNotIn("image", payload)

    def test_image_lists_use_post_and_hide_disabled_base_images(self):
        client = AutodlClient(token="test-only")
        disabled = dict(IMAGE, uuid="off-image", is_off=True)
        with patch.object(client, "request", return_value=[IMAGE, disabled]) as request:
            self.assertEqual(client.get_base_images(), [IMAGE])
            self.assertEqual(request.call_args.args[0], "/api/v1/image/get")
            self.assertEqual(request.call_args.kwargs, {"body": {}})
        with patch.object(client, "request", return_value=[]) as request:
            client.get_private_images()
            self.assertEqual(request.call_args.kwargs, {"body": {}})
        with patch.object(client, "request", return_value=[]) as request:
            client.get_shared_images("PyTorch")
            self.assertEqual(request.call_args.kwargs, {"body": {"reproduction_uuid": "PyTorch"}})

    def test_legacy_labels_resolve_only_when_unambiguous(self):
        labels = [IMAGE[k] for k in ("framework_name", "framework_version", "python_version", "cuda_version")]
        with patch.object(autodl_client, "get_base_images", return_value=[IMAGE]):
            self.assertEqual(resolve_image_info(base_image_labels=labels)["base_image_uuid"], IMAGE["uuid"])
        other_architecture = dict(IMAGE, uuid="different-image", cpu_arch="arm")
        with patch.object(autodl_client, "get_base_images", return_value=[IMAGE, other_architecture]):
            with self.assertRaises(FailedError):
                resolve_image_info(base_image_labels=labels)

    def test_missing_image_fails_before_creation(self):
        with patch.object(autodl_client, "get_base_images", return_value=[]):
            with self.assertRaises(FailedError):
                resolve_image_info(base_image_uuid=IMAGE["uuid"])

    def test_existing_instance_matching_uses_base_image_uuid(self):
        matching = {"base_image_uuid": IMAGE["uuid"]}
        other = {"base_image_uuid": "another-image"}
        with patch.object(autodl_client, "list_instance", return_value=[matching, other]):
            self.assertEqual(get_running_instances(base_image_uuid=IMAGE["uuid"]), [matching])

    def test_image_error_provides_message(self):
        with patch.object(autodl_client, "get_base_images", side_effect=FailedError("请重新登录")):
            with self.assertRaises(app.gr.Error) as error:
                app.load_image_options("base")
        self.assertIn("请重新登录", str(error.exception))


if __name__ == "__main__":
    unittest.main()
