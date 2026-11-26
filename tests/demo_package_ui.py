"""Offline UI demo: python -m tests.demo_package_ui (http://127.0.0.1:7861).

Uses a temporary config/journal, synthetic inventory and simulated orders.
Every AutoDL request is intercepted; this cannot rent real GPUs.
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
import main

sandbox = tempfile.TemporaryDirectory(prefix="hunter-demo-")
main.DATA_DIR = sandbox.name
main.LOGS_DIR = sandbox.name
Path(sandbox.name, "output.log").touch()
from gpuhunter.data_object import Config
from gpuhunter.autodl_client import AutodlClient
from tests.test_package_hunt import machine

Config(web_token="offline-demo-only", gpu_type_names=["RTX 4090"], region_names=["演示地区"],
       gpu_idle_num=1, instance_num=1, image_category="base", base_image_uuid="demo-image",
       package_options={"duration": 1, "per_instance": 80, "total": 80, "wait_minutes": 120}).save()
orders = {}


def fake_request(self, api_url, params=None, method="POST", body=None):
    if api_url.endswith("/region/tag"):
        return [{"region_name": "演示地区", "region_sign": ["test-region"]}]
    if api_url.endswith("/machine/region/gpu_type"):
        return [{"RTX 4090": {"idle_gpu_num": 4, "total_gpu_num": 20}}]
    if api_url.endswith("/image/get"):
        return [{"uuid": "demo-image", "framework_name": "PyTorch", "framework_version": "2.5.1",
                 "python_version": "3.12", "cuda_version": "12.4", "chip_corp": "NVIDIA",
                 "cpu_arch": "amd64", "is_enable": True}]
    if api_url.endswith("/image/private/get"):
        return []
    if api_url.endswith("/user/machine/list"):
        return {"list": [machine(region_name="演示地区", machine_alias="演示主机")], "max_page": 1}
    if api_url.endswith("/order/price/preview"):
        return {**body, "deal_price": 46550}
    if api_url.endswith("/order/instance/create/prepay"):
        identifier = f"demo-order-{len(orders) + 1}"
        orders[identifier] = dict(uuid=identifier, order_type="create_instance", machine_id="test-machine",
                                  charge_type=body["instance_info"]["charge_type"], price_info=body["price_info"],
                                  runtime_info=body["instance_info"], deal_price=46550,
                                  status="unpaid", product_uuid=f"demo-instance-{len(orders) + 1}")
        return {"uuid": identifier}
    if api_url.endswith("/order"):
        return {"order": orders[params["order_uuid"]]}
    if api_url.endswith("/order/pay"):
        order = orders[body["order_uuid"]]
        order["status"] = "paid"
        return order["product_uuid"]
    if api_url.endswith("/instance"):
        return {"list": [dict(uuid=o["product_uuid"], machine_id="test-machine", charge_type=o["charge_type"],
                               req_gpu_amount=o["price_info"]["num"], status="running")
                         for o in orders.values() if o["status"] == "paid"], "max_page": 1}
    raise RuntimeError("Offline demo blocked unsupported API: " + api_url)


if __name__ == "__main__":
    with patch.object(AutodlClient, "request", fake_request), \
            patch("requests.request", side_effect=RuntimeError("External HTTP is disabled in offline demo")):
        import app
        app.demo.launch(server_name="127.0.0.1", server_port=7861, share=False)
