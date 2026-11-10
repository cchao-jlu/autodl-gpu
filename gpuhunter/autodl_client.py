import datetime
import time

import requests

from gpuhunter.utils.helpers import url_set_params
from main import logger

INSTANCE_RUNNING_STATUSES = ["creating", "starting", "running", "re_initializing"]


class FailedError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


WEB_AUTH_MESSAGE = (
    "普通实例的网页登录凭据无效或已过期。请登录 AutoDL 官网，"
    "重新复制请求头 Authorization 的值并在这里验证。开发者 Token 不能用于当前普通实例接口。"
)


class AutodlClient:
    def __init__(self, **kwargs):
        self.api_host = "https://www.autodl.com"
        self.backend_host = "https://fe-config-backend.autodl.com"
        self.token = None
        self.__dict__.update(kwargs)
        self._conf = kwargs

    def load_config(self):
        from gpuhunter.data_object import Config
        config = Config()
        config.load()
        self.token = config.web_token.strip()

    def create_instance(self, machine_id, image="", instance_name="",
                        private_image_uuid="", reproduction_uuid="", reproduction_id=0,
                        req_gpu_amount=1, expand_data_disk=0,
                        clone_instance_uuid=None, copy_data_disk_after_clone=False,
                        keep_src_user_service_address_after_clone=True, base_image_uuid="",
                        **kwargs):
        """
        :param machine_id: "463e49a218"
        :param image: "hub.kce.ksyun.com/autodl-image/torch:cuda11.0-cudnn8-devel-ubuntu18.04-py38-torch1.7.0"
        :param instance_name: "my-instance-name"
        :param private_image_uuid: "image-f1389xxxxx"
        :param reproduction_uuid: "RVC-Boss/GPT-SoVITS/GPT-SoVITS-Official:v9"
        :param reproduction_id: 761
        :param req_gpu_amount: 1
        :param expand_data_disk: 1073741824 (单位B，1GB=1073741824)
        :param clone_instance_uuid: 要复制的实例 ID
        :param copy_data_disk_after_clone: 是否复制数据盘
        :param keep_src_user_service_address_after_clone: 是否保持服务地址
        :return: 3a5440b582-e99c9237 (新的 instance_uuid)
        """
        api = "/api/v1/order/instance/create/payg"
        body = {
            "instance_info": {
                "machine_id": machine_id,
                "charge_type": "payg",
                "req_gpu_amount": req_gpu_amount,
                "base_image_uuid": base_image_uuid,
                "private_image_uuid": private_image_uuid,
                "reproduction_uuid": reproduction_uuid,
                "instance_name": instance_name,
                "expand_data_disk": expand_data_disk,
                "reproduction_id": reproduction_id,
            },
            "price_info": {
                "coupon_id_list": [],
                "machine_id": machine_id,
                "charge_type": "payg",
                "duration": 1,
                "num": req_gpu_amount,
                "expand_data_disk": expand_data_disk
            },
            **kwargs,
        }
        if image:
            body["instance_info"]["image"] = image
        if clone_instance_uuid:
            api = "/api/v1/order/instance/clone/payg"
            body["instance_uuid"] = clone_instance_uuid
            body["instance_info"] = {
                **body["instance_info"],
                "copy_data_disk_after_clone": copy_data_disk_after_clone,
                "keep_src_user_service_address_after_clone": keep_src_user_service_address_after_clone
            }
        return self.request(api, body=body)

    def update_instance_shutdown(self, instance_uuid, shutdown_at, **kwargs):
        api = "/api/v1/instance/timed/shutdown"
        body = {
            "instance_uuid": instance_uuid,
            "shutdown_at": (shutdown_at.strftime("%Y-%m-%d %H:%M")
                            if isinstance(shutdown_at, (datetime.datetime, datetime.date))
                            else shutdown_at),
            **kwargs,
        }
        self.request(api, body=body)

    def update_instance_name(self, instance_uuid, instance_name, **kwargs):
        api = "/api/v1/instance/name"
        body = {
            "instance_uuid": instance_uuid,
            "instance_name": instance_name,
            **kwargs,
        }
        self.request(api, body=body, method="PUT")

    def get_private_images(self, **kwargs):
        """
        :return: [
          {
            "id": 224747,
            "created_at": "2023-11-27T00:32:01+08:00",
            "updated_at": "2023-11-27T00:32:01+08:00",
            "uid": 230135,
            "image_uuid": "image-f1389xxxxx",
            "machine_id": "fc604xxxxx",
            "runtime_uuid": "autodl-container-fc604xxxxx-xxxxxxxx",
            "instance_uuid": "fc604xxxxx-xxxxxxxx",
            "name": "xxxxxxx-xxxx-xxxx",
            "storage_oss_sign": "oss-cn-ningxia-image-hub",
            "bucket_name": "online-private-image",
            "object_name": "fc604xxxxx-xxxxxxxx-xxxxxx.tar",
            "image_size": 11193702400,
            "read_layer_image_name": "hub.kce.ksyun.com/autodl-image/miniconda:cuda11.8-cudnn8-devel-ubuntu22.04-py310",
            "progress_info": null,
            "progress_content": {
              "oss_file_size": 11193702400,
              "progress": 100,
              "error": ""
            },
            "status": "finished",
            "share_image_type": 0,
            "share_image_uuid": "",
            "share_image_user": "",
            "user_phone": "",
            "python_v": "3.10(ubuntu22.04)",
            "cuda_v": "11.8",
            "StorageOSS": null
          },
        ]
        """
        api = "/api/v1/image/private/get"
        params = {
            **kwargs,
        }
        return self.request(api, body=params)

    def get_shared_images(self, reproduction_uuid="", **kwargs):
        """
        :param reproduction_uuid:
        :param kwargs:
        :return: [
          {
            "image_id": 332,
            "uuid": "chatchat-space/Langchain-Chatchat/Langchain-Chatchat",
            "username": "glide-the",
            "reproduce_count": 12680,
            "describe": "基于 Langchain 与 ChatGLM 等语言模型的本地知识库问答",
            "avatar": "https://codewithgpu-image-1310972338.cos.ap-beijing.myqcloud.com/115767-499182437-yMR8GzCEAGX5fOkrWVHn.png",
            "popular_type": "excellent",
            "version_info": [
              {
                "version": "0.2.10",
                "public_image_size": "29185730560",
                "cuda_v": "12.2",
                "framework": "PyTorch",
                "framework_v": "12.0",
                "created_at": "2024-01-26T10:16:24+08:00",
                "reproduce_count": 1770
              },
              ...
            ],
          },
        ]
        """
        api = "/api/v1/image/codewithgpu/list"
        params = {
            "reproduction_uuid": reproduction_uuid,
            **kwargs,
        }
        return self.request(api, body=params)

    def get_shared_image_detail(self, image_uuid, image_version, image_id, **kwargs):
        """
        :param image_uuid: "chatchat-space/Langchain-Chatchat/Langchain-Chatchat"
        :param image_version: "0.2.10"
        :param image_id: 332
        :return: {
          "username": "glide-the",
          "entity_uuid": "chatchat-space/Langchain-Chatchat/Langchain-Chatchat:v0.2.10",
          "entity_id": 332,
          "image": "hub.kce.ksyun.com/autodl-image/tensorflow:cuda11.2-cudnn8-devel-ubuntu20.04-py38-tf2.9.0",
          "python_v": "3.8(ubuntu20.04)",
          "cuda_v": "11.2",
          "framework": "",
          "framework_v": "",
          "created_at": "0001-01-01T00:00:00Z"
        }
        """
        api = "/api/v1/image/codewithgpu"
        params = {
            "reproduction_uuid": f"{image_uuid}:v{image_version}",
            "reproduction_id": image_id,
            **kwargs,
        }
        return self.request(api, params=params, method="GET")

    def get_base_images(self, **kwargs):
        """Return enabled base images from the current flat catalog.

        Each item contains uuid, framework_name, framework_version,
        python_version, cuda_version, chip_corp and cpu_arch.
        """
        api = "/api/v1/image/get"
        params = {
            **kwargs,
        }
        images = self.request(api, body=params)
        return [image for image in images if not image.get("is_off")]

    def list_instance(self, status=None, **kwargs):
        """
        :param status: creating | starting | running | re_initializing | ...
        :return: [
          {
            "uuid": "eaf141be95-b0475166",
            "machine_id": "eaf141be95",
            "machine_alias": "091机",
            "region_sign": "west-X",
            "region_name": "西北企业区",
            "disk_expand_available": 2040109465600,
            "highest_cuda_version": "12.2",
            "status": "shutdown",
            ...
            "image": "hub.kce.ksyun.com/autodl-image/miniconda:cuda11.8-cudnn8-devel-ubuntu22.04-py310",
            "private_image_uuid": "",
            "reproduction_uuid": "comfyanonymous/ComfyUI/ComfyUI-SDXL09-12NewVersion-bobo-animatediff:v5",
            "reproduction_id": 471,
            "timed_shutdown_at": {
              "Time": "0001-01-01T00:00:00Z",
              "Valid": false
            },
            ...
          },
        ]
        """
        api = "/api/v1/instance"
        body = {
            "page_size": 20,
            "status": status if isinstance(status, (list, tuple)) else [status] if status else [],
            **kwargs,
        }
        return self.list_request(api, body)

    def get_regions(self, **kwargs):
        """
        :return: [
          {
            "region_sign": [
              "west-B",
              "west-C"
            ],
            "region_name": "西北B区",
            "data_center": "westDC3",
            "visible": "all",
            "used_for": "all",
            "setting": {
              "clone_instance": true
            },
            "clone_instance": true,
            "tag_name": "",
            "tag_color": "#FF0000"
          }
        ]
        """
        api = "https://fe-config-backend.autodl.com/api/v1/autodl/region/tag"
        params = {
            **kwargs,
        }
        return self.request(api, params=params, method="GET")

    def get_region_gpu_types(self, region_sign_list, **kwargs):
        """
        :param region_sign_list: ["beijing-A", "beijing-B", "beijing-D", "beijing-E"]
        :param kwargs:
        :return: [
          {
            "RTX 4090": {
                "idle_gpu_num": 3,
                "total_gpu_num": 2686
            }
          },
          {
            "RTX 3090": {
                "idle_gpu_num": 0,
                "total_gpu_num": 444
            }
          },
        ],
        """
        api = "/api/v1/machine/region/gpu_type"
        body = {
            **kwargs,
            "region_sign_list": region_sign_list,
        }
        return self.request(api, body=body)

    def list_machine(self, region_sign_list, gpu_type_name, gpu_idle_num=1, **kwargs):
        """
        获取 GPU 主机列表，使用 list[0]["gpu_order_num"] 判断是否可可租。
        :param region_sign_list: ["beijing-A", "beijing-B"]
        :param gpu_type_name: ["RTX 4090", "RTX 3090"]
        :param gpu_idle_num:
        :return: [
          {
            "machine_id": "2dd949a4cf",
            "machine_name": "agent-116-172-66-240",
            "machine_alias": "063机",
            ...
            "region_name": "西北B区",
            "region_sign": "west-B",
            "gpu_name": "RTX 4090",
            "gpu_order_num": 0,
            ...
            "highest_cuda_version": "12.2",
            "cpu_per_gpu": 22,
            "mem_per_gpu": 96636764160,
            "payg": true,
            "payg_price": 2720,
            "max_data_disk_expand_size": 2147483648000,
          },
        ]
        """
        api = "/api/v1/user/machine/list"
        body = {
            "charge_type": "payg",
            "page_size": 20,
            "gpu_idle_num": gpu_idle_num,
            "region_sign_list": region_sign_list,
            "gpu_type_name": gpu_type_name if isinstance(gpu_type_name, (list, tuple)) else [gpu_type_name],
            **kwargs,
        }
        return self.list_request(api, body)

    def get_instance_snapshot(self, instance_uuid, **kwargs):
        """
        :return: {
            "image_type": "public",
            "image": {
              "image_name": "hub.kce.ksyun.com/autodl-image/torch:cuda10.0-cudnn7-devel-ubuntu18.04-py37-torch1.1.0",
              ...
            },
            "payg_price": 9536,
            "origin_pay_price": 10040,
            "snapshot_gpu_alias_name": "RTX 4090",
            "machine_info_snapshot": {
              "cpu_num": 128,
              "disk_size": 7679228837888,
              "gpu_type": {
                "name": "RTX 4090",
                "memory": 25769803776
              },
              ...
              "machine_sku": [
                {
                  "type": "payg",
                  "origin_price": 0,
                  "current_price": 2510,
                  ...
                },
              ],
              "region_sign": "west-B",
              "region_name": "西北B区"
            },
            ...
          }
        """
        api = "/api/v1/instance/snapshot"
        params = {
            "instance_uuid": instance_uuid,
            **kwargs,
        }
        return self.request(api, params=params, method="GET")

    def list_request(self, api, body):
        page_index = 1
        while True:
            body["page_index"] = page_index
            data = self.request(api, body=body)
            for item in data["list"]:
                yield item
            page_index += 1
            if page_index > data["max_page"]:
                break
            time.sleep(0.2)

    def request(self, api_url, params=None, method="POST", body=None):
        if not self.token:
            raise FailedError("请先在本地页面填写并验证普通实例的网页登录凭据。", code="MissingWebToken")
        url = api_url if api_url.startswith("https://") else f"{self.api_host}{api_url}"
        if params:
            url = url_set_params(url, **params)
        headers = {
            "Authorization": self.token,
            "Content-Type": "application/json"
        }
        logger.debug(url)
        logger.debug(method)
        logger.debug(body)
        response = requests.request(method, url, json=body, headers=headers, timeout=(5, 20))
        if response.status_code in (401, 403):
            raise FailedError(WEB_AUTH_MESSAGE, code="AuthorizeFailed")
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError:
            raise FailedError("AutoDL 返回了非 JSON 响应，请稍后重试。") from None
        if not isinstance(result, dict):
            raise FailedError("AutoDL 返回格式异常，请稍后重试。")
        if result.get("code") not in ["Success", "OK"]:
            code = result.get("code")
            if code == "AuthorizeFailed":
                raise FailedError(WEB_AUTH_MESSAGE, code=code)
            message = str(result.get("msg") or "AutoDL 请求失败").replace(self.token, "[凭据已隐藏]")
            logger.error("AutoDL request failed: %s", code)
            raise FailedError(message, code=code)
        else:
            return result["data"]


def get_default_client():
    client = AutodlClient()
    client.load_config()
    return client


def get_available_machines(region_sign_list, gpu_type_name, gpu_idle_num=1, count=10, min_expand_data_disk=0, **kwargs):
    machines = []
    for mch in autodl_client.list_machine(region_sign_list, gpu_type_name, gpu_idle_num, **kwargs):
        if mch["gpu_idle_num"] >= gpu_idle_num \
                and mch["gpu_order_num"] >= gpu_idle_num \
                and mch["max_data_disk_expand_size"] >= min_expand_data_disk:
            machines.append(mch)
        if count is not None and len(machines) == count:
            break
    return machines


def get_running_instances(region_names=None, gpu_type_names=None, image=None, private_image_uuid=None,
                          reproduction_uuid=None, reproduction_id=None, base_image_uuid=None):
    def match(inst):
        return (region_names is None or inst["region_name"] in region_names) \
            and (gpu_type_names is None or inst["snapshot_gpu_alias_name"] in gpu_type_names) \
            and (image is None or inst["image"] == image) \
            and (not base_image_uuid or inst.get("base_image_uuid") == base_image_uuid) \
            and (private_image_uuid is None or inst["private_image_uuid"] == private_image_uuid) \
            and (reproduction_uuid is None or inst["reproduction_uuid"] == reproduction_uuid) \
            and (reproduction_id is None or inst["reproduction_id"] == reproduction_id)

    return [
        inst
        for inst in autodl_client.list_instance(INSTANCE_RUNNING_STATUSES)
        if match(inst)
    ]


def resolve_image_info(base_image_labels=None, shared_image_keyword=None,
                       shared_image_username_keyword=None, shared_image_version=None,
                       private_image_uuid=None, private_image_name=None, base_image_uuid=None):
    image_info = {
        "image": "",
        "base_image_uuid": "",
        "private_image_uuid": "",
        "reproduction_uuid": "",
        "reproduction_id": 0,
    }
    if base_image_uuid or base_image_labels:
        fields = ("framework_name", "framework_version", "python_version", "cuda_version")
        images = autodl_client.get_base_images()
        matches = [image for image in images if (
            image["uuid"] == base_image_uuid if base_image_uuid else
            [image.get(field, "") for field in fields] == list(base_image_labels)
        )]
        if len(matches) != 1:
            raise FailedError("所选基础镜像已下架或无法唯一确定，请在页面重新选择基础镜像。")
        image_info["base_image_uuid"] = matches[0]["uuid"]
    elif shared_image_keyword:
        filtered_image = [i for i in autodl_client.get_shared_images(shared_image_keyword)
                          if (not shared_image_username_keyword
                              or shared_image_username_keyword.lower() in i["username"].lower())]
        if len(filtered_image) == 0:
            raise ValueError(f"Image not found with keyword: {shared_image_keyword!r}"
                             f" and {shared_image_username_keyword!r}")
        image = filtered_image[0]
        filtered_versions = [v for v in image["version_info"]
                             if (not shared_image_version
                                 or v["version"].startswith(shared_image_version.strip("v")))]
        if len(filtered_versions) == 0:
            raise ValueError(f"Image not found with version: {shared_image_version!r}")
        image_version = filtered_versions[0]["version"]
        shared_image = autodl_client.get_shared_image_detail(image["uuid"], image_version, image["image_id"])
        image_info = {
            **image_info,
            "image": shared_image["image"],
            "reproduction_uuid": shared_image["entity_uuid"],
            "reproduction_id": shared_image["entity_id"],
        }
    elif private_image_uuid or private_image_name:
        filtered_image = [i for i in autodl_client.get_private_images()
                          if (private_image_uuid and i["image_uuid"] == private_image_uuid
                              or private_image_name and i["name"] == private_image_name)]
        if len(filtered_image) == 0:
            raise ValueError(f"Image not found with uuid: {private_image_uuid!r}"
                             f" or name: {private_image_name!r}")
        private_image = filtered_image[0]
        image_info = {
            **image_info,
            "image": private_image.get("read_layer_image_name", ""),
            "private_image_uuid": private_image["image_uuid"],
        }
    else:
        raise ValueError(f"Invalid parameters")
    return image_info


autodl_client = get_default_client()
