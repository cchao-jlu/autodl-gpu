import json
import os.path
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

import gradio as gr
from requests import RequestException

from gpuhunter.autodl_client import AutodlClient, FailedError, autodl_client
from gpuhunter.data_object import RegionList, Config
from gpuhunter.utils.helpers import json_dumps, validate_email
from main import LOGS_DIR, logger, DATA_DIR

css = """
.block.error-message, .block.success-message { padding: var(--block-padding); }
.block.error-message p, .block.success-message p { font-weight: bold; margin: 0; }
.error-message ul li, .block.error-message p { color: var(--error-icon-color);}
"""
with gr.Blocks(title="AutoDL GPU Hunter", theme=gr.themes.Default(text_size="lg"), css=css) as demo:
    gr_is_token_ready = gr.Radio(value=False, visible=False)
    gr.Markdown(
        """
        # 🐒 AutoDL GPU Hunter
        这是一个卡蹲蹲！帮你蹲守 AutoDL 显卡主机，请设置以下参数开始蹲守。官网链接：[算力市场](https://www.autodl.com/market/list)
        | [容器实例](https://www.autodl.com/console/instance/list)
        """
    )

    with gr.Group(visible=False) as gr_token_input_group:
        gr.Markdown("### 普通容器实例登录\n请使用已登录官网的**网页登录凭据**。开发者 Token 不适用于当前普通实例接口。")
        gr_token_input = gr.Textbox(
            label="网页登录凭据（Authorization）",
            type="password",
            lines=1,
            info="仅保存在本机 runtime/data/config.json。请勿把凭据发到聊天、截图或公开仓库。",
            placeholder="粘贴官网请求头中 Authorization 的值"
        )
        with gr.Accordion("如何获取网页登录凭据", open=True):
            gr.Markdown("""
1. 在浏览器登录 [AutoDL 官网控制台](https://www.autodl.com/console/instance/list)。
2. 按 **F12**（或 **Ctrl+Shift+I**），打开 **Network / 网络**，选择 **Fetch/XHR** 后刷新页面。
3. 选中请求地址为 `https://www.autodl.com/api/v1/instance` 的请求；也可选其他已成功的普通实例接口请求。
4. 在 **Headers / 标头 → Request Headers / 请求标头** 中，复制 **Authorization 的值**，粘贴到上方。

如果没找到请求头，可在 **Application / 应用 → Local Storage / 本地存储 → https://www.autodl.com** 中找到 `token`，复制其 Value。

验证按钮只查询地区、GPU 和实例列表。选择显卡和镜像后点击“开始蹲守”，才会尝试创建付费实例。网页登录凭据过期后需重新获取。
""")
        gr_token_save_button = gr.Button("验证并保存网页登录凭据", variant="primary", size="lg")

    with gr.Group(visible=False) as gr_token_view_group:
        gr_token_view_input = gr.Textbox(label="普通实例登录状态", lines=1, interactive=False)
        gr_token_clear_button = gr.Button("退出", variant="secondary", size="sm")

    gr_token_input_error = gr.Markdown(elem_classes=["error-message"], visible=False)

    with gr.Tab("🌲 开始蹲守", visible=False) as gr_config_tab:
        with gr.Row():
            with gr.Column():
                with gr.Group():
                    gr_gpu_types = gr.CheckboxGroup(label="显卡型号")
                    gr_regions = gr.CheckboxGroup(label="地区")
                    gr_gpu_num = gr.Radio(choices=[n for n in range(1, 13)], label="GPU 个数", value=1)

            with gr.Column():
                gr_instance_num = gr.Slider(label="租用 GPU 主机数量", info="可选择 1-20 台", minimum=0, maximum=20,
                                            step=1, value=1)
                with gr.Group():
                    gr_image_category = gr.Radio(
                        choices=[("基础镜像", "base"), ("社区镜像", "shared"), ("我的镜像", "private")],
                        label="启动镜像",
                    )
                    with gr.Group(visible=False) as gr_base_image_group:
                        gr_base_image = gr.Dropdown(show_label=False, info="请选择镜像")
                    with gr.Group(visible=False) as gr_shared_image_group:
                        gr_shared_image_search = gr.Textbox(show_label=False, info="请填写关键字查找镜像")
                        gr_shared_image = gr.Dropdown(show_label=False, info="请选择镜像")
                    with gr.Group(visible=False) as gr_private_image_group:
                        gr_private_image = gr.Dropdown(show_label=False, info="请选择镜像")

                with gr.Accordion(open=False) as gr_expand_disk_accordion:
                    gr_expand_disk_gb = gr.Slider(
                        info="可选择扩容范围 0-3000 GB，按此扩容量挑选机器并要求扩容",
                        minimum=0,
                        maximum=3000,
                        step=1,
                        value=0,
                        show_label=False,
                    )

                with gr.Accordion(open=False) as gr_clone_instances_accordion:
                    with gr.Group():
                        with gr.Row():
                            gr_clone_instances = gr.Dropdown(
                                label="选择实例",
                                info="只能克隆同地区的实例，请确保实例已关机。如蹲守多个地区，"
                                     "可以为每个地区选择一个要克隆的实例，蹲守到主机后会按照主机所在地区进行匹配",
                                min_width=550,
                                multiselect=True
                            )
                            gr_clone_instances_refresh_button = gr.Button("刷新", size="sm", min_width=50)
                        gr_copy_data_after_clone = gr.Checkbox(label="克隆后复制数据盘", value=True)
                        gr_keep_address_after_clone = gr.Checkbox(label="克隆后保持服务网址", value=True)

                with gr.Accordion(open=False) as gr_shutdown_time_accordion:
                    gr_shutdown_time_type = gr.Radio(
                        show_label=False,
                        choices=["今晚 23:59", "8 小时", "12 小时", "24 小时", "不关机"],
                        value="今晚 23:59",
                        info="创建实例时自动设置定时关机，防止忘记关闭后产生费用。")

                with gr.Accordion(open=False) as gr_email_notify_accordion:
                    with gr.Group():
                        with gr.Row(equal_height=True):
                            with gr.Group():
                                gr_email_notify_sender = gr.Textbox(label="发信/收信邮箱", type="email")
                                gr_email_notify_smtp_password = gr.Textbox(
                                    label="发信密码", type="password",
                                    info="以明文方式保存在此机，请确保环境安全再填写！如果你的邮箱很重要，不推荐直接填写账号密码。"
                                         "很多邮箱都支持单独的发信密码 (或叫授权码等)，推荐使用这类临时密码。"
                                         "实在不行就新注册一个免费邮箱！"
                                )
                            gr_email_notify_smtp_server = gr.Textbox(
                                label="SMTP 服务器",
                                info="如果不知道该怎么填，可以登录你的邮箱网站查看帮助，一般在发信设置里面可以找到 SMTP 设置方法。"
                                     "常见邮箱设置："
                                     "- QQ 邮箱：smtp.qq.com "
                                     "- 163 邮箱：smtp.163.com "
                            )
                    gr_email_notify_send_button = gr.Button("发送测试邮件", size="sm")
                    gr_email_notify_send_output = gr.Markdown(visible=False)

                with gr.Accordion("更多选项", open=False):
                    gr_scan_interval = gr.Slider(
                        minimum=10,
                        maximum=600,
                        value=30,
                        step=1,
                        label="扫描间隔 (秒)",
                        info="默认：30 秒，可选 10-600 秒。如果不是急需，请设置大一点，"
                             "因为显卡空出来也需要时间，同时也可以减少官网的访问压力。"
                    )
                    gr_shutdown_hunter_after_success = gr.Radio(
                        choices=[("关机", True), ("不关机", False)],
                        value=False,
                        label="守到后将 Hunter 关机",
                        info="成功后可以关闭运行此程序 (AutoDL GPU Hunter) 的机器，防止重复蹲守浪费资源。"
                    )

        gr_hunting_start_button = gr.Button("🙈 开始蹲守", variant="primary", size="lg")
        gr_hunting_error = gr.Markdown(elem_classes=["error-message"], visible=False)
        gr_hunting_stop_button = gr.Button("🤚 停止", variant="stop", size="lg", visible=False)
        gr_hunting_logs = gr.Textbox(label="🙉 正在蹲守", autoscroll=True, lines=10, visible=False)


        def load_region_options(gpu_type_names=None):
            region_list = RegionList().load()
            return {
                gr_regions: gr.CheckboxGroup(
                    choices=[
                        (f'{r["region_name"]} ({r["idle_gpu_num"]})', r["region_name"])
                        for r in region_list.get_region_stats(gpu_types=gpu_type_names or [])
                        if r["total_gpu_num"] > 0
                    ],
                ),
            }


        def load_gpu_options():
            region_list = RegionList().load()
            return {
                gr_gpu_types: gr.CheckboxGroup(
                    choices=[
                        (f'{g["gpu_type"]} ({g["idle_gpu_num"]})', g["gpu_type"])
                        for g in region_list.get_gpu_stats()
                        if g["total_gpu_num"] > 0
                    ],
                ),
            }


        def load_gpu_region_options():
            return {
                **load_gpu_options(),
                **load_region_options(),
            }


        def _load_image_options(image_category):
            image_option_groups = {
                gr_base_image_group: gr.Group(visible=False),
                gr_shared_image_group: gr.Group(visible=False),
                gr_private_image_group: gr.Group(visible=False),
                gr_base_image: gr.Dropdown(choices=[], value=None),
                gr_shared_image: gr.Dropdown(choices=[], value=None),
                gr_private_image: gr.Dropdown(choices=[], value=None),
            }
            if image_category == "base":
                base_images = autodl_client.get_base_images()
                return {
                    **image_option_groups,
                    gr_base_image_group: gr.Group(visible=True),
                    gr_base_image: gr.Dropdown(
                        choices=[
                            (" / ".join([
                                image["framework_name"], image["framework_version"],
                                image["python_version"], f'CUDA/运行时 {image["cuda_version"]}',
                                image.get("chip_corp", ""), image.get("cpu_arch", ""),
                            ]) + (" / 桌面版" if image.get("is_desktop") else ""),
                             json_dumps({"base_image_uuid": image["uuid"]}))
                            for image in base_images
                        ],
                        allow_custom_value=False,
                    )
                }
            elif image_category == "shared":
                return {
                    **image_option_groups,
                    gr_shared_image_group: gr.Group(visible=True),
                }
            elif image_category == "private":
                private_images = autodl_client.get_private_images()
                return {
                    **image_option_groups,
                    gr_private_image_group: gr.Group(visible=True),
                    gr_private_image: gr.Dropdown(
                        choices=[
                            (f'{i["name"]} ({i["image_uuid"]})',
                             json_dumps({
                                 "private_image_uuid": i["image_uuid"],
                                 "private_image_name": i["name"],
                             }))
                            for i in private_images
                        ],
                        allow_custom_value=False,
                    )
                }
            else:
                return image_option_groups


        def load_image_options(image_category):
            try:
                return _load_image_options(image_category)
            except FailedError as error:
                raise gr.Error(str(error)) from None
            except RequestException:
                raise gr.Error("镜像列表加载失败，请检查网络后重新选择镜像类型。") from None


        def load_shared_image_options(shared_image_search):
            shared_images = autodl_client.get_shared_images(shared_image_search)
            return {
                gr_shared_image: gr.Dropdown(
                    choices=[
                        (f'{i["uuid"]} / v{v["version"]} ({i["username"]})',
                         json_dumps({
                             "shared_image_keyword": i["uuid"],
                             "shared_image_username_keyword": i["username"],
                             "shared_image_version": str(v["version"]),
                         }))
                        for i in shared_images
                        for v in i["version_info"]
                    ],
                    allow_custom_value=False,
                ),
            }


        def update_disk_accordion(expand_disk_gb):
            return {
                gr_expand_disk_accordion: gr.Accordion(
                    label=f"数据盘：免费 50 GB + 扩容 {expand_disk_gb} GB" if expand_disk_gb > 0 else "扩容数据盘"
                )
            }


        def load_clone_instances_options():
            def get_label(image):
                return f'{image["region_name"]} / {image["machine_alias"]} ({image["uuid"]})'

            return {
                gr_clone_instances: gr.Dropdown(
                    choices=[
                        *[
                            (get_label(i),
                             json_dumps({
                                 "label": get_label(i),
                                 "uuid": i["uuid"],
                                 "region_name": i["region_name"],
                                 "region_sign": i["region_sign"],
                             }))
                            for i in autodl_client.list_instance("shutdown")
                        ]
                    ]
                )
            }


        def update_clone_instances_accordion(clone_instances):
            title_suffix = ""
            if clone_instances:
                if len(clone_instances) == 1:
                    clone_instances_info = json.loads(clone_instances[0])
                    clone_instances_label = clone_instances_info["label"]
                    title_suffix = f"：{clone_instances_label}" if clone_instances_label else ""
                else:
                    title_suffix = f'：{"、".join([json.loads(i)["label"] for i in clone_instances])}'
            return {
                gr_clone_instances_accordion: gr.Accordion(label=f'克隆现有实例{title_suffix}')
            }


        def update_shutdown_time_accordion(shutdown_time_type):
            return {
                gr_shutdown_time_accordion: gr.Accordion(
                    f'定时关机{f"：{shutdown_time_type}" if shutdown_time_type else ""}'
                )
            }


        def update_email_notify_accordion(email_notify_sender):
            return {
                gr_email_notify_accordion: gr.Accordion(
                    f'邮件通知{f"：{email_notify_sender}" if email_notify_sender else ""}'
                ),
            }


        def update_email_config(config, email_notify_sender, email_notify_smtp_server, email_notify_smtp_password):
            if email_notify_sender:
                from gpuhunter.utils.mail import normalize_smtp_server
                config.mail_notify = True
                config.mail_receipt = email_notify_sender
                config.mail_sender = email_notify_sender
                config.mail_smtp_host, config.mail_smtp_port = normalize_smtp_server(email_notify_smtp_server)
                config.mail_smtp_username = email_notify_sender
                config.mail_smtp_password = email_notify_smtp_password
            else:
                config.mail_notify = False
                config.mail_receipt = ""
                config.mail_sender = ""
                config.mail_smtp_host = ""
                config.mail_smtp_port = ""
                config.mail_smtp_username = ""
                config.mail_smtp_password = ""


        def send_test_email(email_notify_sender, email_notify_smtp_password, email_notify_smtp_server):
            error_message = None
            if not email_notify_sender:
                error_message = "请填写发信/收信邮箱"
            elif not email_notify_smtp_password:
                error_message = "请填写发信密码"
            elif not email_notify_smtp_server:
                error_message = "SMTP 服务器"
            if not error_message:
                from gpuhunter.utils.mail import normalize_smtp_server, send_mail
                from smtplib import SMTPException
                smtp_host, smtp_port = normalize_smtp_server(email_notify_smtp_server)
                try:
                    send_mail(
                        email_notify_sender,
                        "[GPUHunter] 可以收到邮件！",
                        content="恭喜！看到这份邮件说明你的邮件通知设置没有问题。",
                        sender=email_notify_sender,
                        smtp_host=smtp_host,
                        smtp_port=smtp_port,
                        smtp_username=email_notify_sender,
                        smtp_password=email_notify_smtp_password,
                    )
                    gr.Info("邮件发送成功！")
                    config = Config().load()
                    update_email_config(
                        config,
                        email_notify_sender,
                        email_notify_smtp_server,
                        email_notify_smtp_password
                    )
                    config.save()
                except SMTPException as e:
                    error_message = f"发信失败：{str(e)}"
            return {
                **update_email_notify_accordion(email_notify_sender),
                gr_email_notify_send_output:
                    gr.Markdown(
                        error_message,
                        visible=True,
                        elem_classes=["error-message"]
                    ) if error_message
                    else gr.Markdown(
                        "邮件发送成功！请登录邮箱查收新邮件。",
                        visible=True,
                        elem_classes=["success-message"]
                    )
            }


        def hunting_start(gpu_types, regions, gpu_num, instance_num, image_category, base_image, shared_image,
                          private_image, expand_disk_gb, clone_instances, copy_data_after_clone,
                          keep_address_after_clone, shutdown_time_type, email_notify_sender,
                          email_notify_smtp_password, email_notify_smtp_server, scan_interval,
                          shutdown_hunter_after_success):
            error_messages = []
            if not gpu_types:
                error_messages.append("请选择显卡型号")
            if not regions:
                error_messages.append("请选择地区")
            if not gpu_num or gpu_num <= 0 or gpu_num > 12:
                error_messages.append("请选择有效的 GPU 个数")
            if not instance_num or instance_num <= 0 or instance_num > 20:
                error_messages.append("请选择有效的 GPU 主机数量")
            if not image_category:
                error_messages.append("请选择启动镜像")
            else:
                if image_category == "base" and not base_image \
                        or image_category == "shared" and not shared_image \
                        or image_category == "private" and not private_image:
                    error_messages.append("请选择镜像")
            if not shutdown_time_type:
                error_messages.append("请选择是否定时关机")
            if email_notify_sender:
                try:
                    validate_email(email_notify_sender)
                except ValueError:
                    error_messages.append("邮件地址不正确")
                if not email_notify_smtp_password:
                    error_messages.append("请填写发信密码")
                if not email_notify_smtp_server:
                    error_messages.append("请填写 SMTP 服务器地址")

            if len(error_messages) == 0:
                config = Config().load()

                config.region_names = regions
                config.gpu_type_names = gpu_types
                config.gpu_idle_num = gpu_num
                config.instance_num = instance_num

                config.image_category = image_category
                config.base_image_labels = []
                config.base_image_uuid = ""
                config.shared_image_keyword = ""
                config.shared_image_username_keyword = ""
                config.shared_image_version = ""
                config.private_image_uuid = ""
                config.private_image_name = ""
                image_info_json = None
                if image_category == "base":
                    image_info_json = base_image
                elif image_category == "shared":
                    image_info_json = shared_image
                elif image_category == "private":
                    image_info_json = private_image
                if image_info_json:
                    image_info = json.loads(image_info_json)
                    for k, v in image_info.items():
                        setattr(config, k, v)

                config.expand_data_disk = expand_disk_gb * 1073741824

                if clone_instances:
                    config.clone_instances = [json.loads(i) for i in clone_instances]
                    config.copy_data_disk_after_clone = copy_data_after_clone
                    config.keep_src_user_service_address_after_clone = keep_address_after_clone
                else:
                    config.clone_instances = []
                    config.copy_data_disk_after_clone = False
                    config.keep_src_user_service_address_after_clone = False

                config.shutdown_instance_today = False
                config.shutdown_instance_after_hours = 0
                if shutdown_time_type == "今晚 23:59":
                    config.shutdown_instance_today = True
                elif shutdown_time_type.endswith("小时"):
                    config.shutdown_instance_after_hours = int(re.findall("^\\d+", shutdown_time_type)[0])

                config.shutdown_hunter_after_finished = shutdown_hunter_after_success
                config.retry_interval_seconds = max(10, scan_interval)

                update_email_config(config, email_notify_sender, email_notify_smtp_server, email_notify_smtp_password)

                # 保存设置
                config.save()

                outputs = {
                    gr_hunting_start_button: gr.Button(visible=False),
                    gr_hunting_error: gr.Markdown(visible=False),
                    gr_hunting_stop_button: gr.Button(visible=True),
                    gr_hunting_logs: gr.Textbox(visible=True),
                }
                yield outputs
                # 创建运行状态标记
                Path(os.path.join(DATA_DIR, "running.state")).touch()
                # 重新开启输出日志
                output_log_file = os.path.join(LOGS_DIR, "output.log")
                shutil.copy(output_log_file, f'{output_log_file}.{datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")}')
                with open(output_log_file, "w") as f:
                    f.truncate()
                    f.close()
                running_file = Path(os.path.join(DATA_DIR, "running.state"))
                error_message = ""
                try:
                    while running_file.exists():
                        from gpuhunter.commands.wait import try_to_create_instances
                        if try_to_create_instances():
                            break
                        logger.info(
                            f"等待 {config.retry_interval_seconds!r} 秒后重新扫描..."
                            f" Next scanning will start after {config.retry_interval_seconds!r} seconds..."
                        )
                        yield outputs
                        time.sleep(config.retry_interval_seconds)
                except FailedError as e:
                    error_message = str(e)
                except RequestException:
                    error_message = "AutoDL 网络请求失败，已停止蹲守。请先在官网确认实例状态，再重新开始。"
                finally:
                    running_file.unlink(missing_ok=True)
                yield {
                    gr_hunting_start_button: gr.Button(visible=True),
                    gr_hunting_stop_button: gr.Button(visible=False),
                    gr_hunting_error: gr.Markdown(visible=bool(error_message), value=error_message),
                    gr_hunting_logs: gr.Textbox(visible=True),
                }
            else:
                yield {
                    gr_hunting_start_button: gr.Button(visible=True),
                    gr_hunting_error: gr.Markdown(visible=True, value="\n".join([f"- {m}" for m in error_messages])),
                    gr_hunting_stop_button: gr.Button(visible=False),
                    gr_hunting_logs: gr.Textbox(visible=False),
                }


        def hunting_stop():
            # 重置运行标记
            Path(os.path.join(DATA_DIR, "running.state")).unlink(missing_ok=True)
            return {
                gr_hunting_start_button: gr.Button(visible=True),
                gr_hunting_error: gr.Markdown(visible=False, value=""),
                gr_hunting_stop_button: gr.Button(visible=False),
                gr_hunting_logs: gr.Textbox(visible=False),
            }


        def read_output_logs():
            output_log_file = os.path.join(LOGS_DIR, "output.log")
            if os.path.exists(output_log_file):
                with open(output_log_file, "r") as f:
                    return f.read()
            else:
                return ""


        def check_hunting_status():
            is_hunting = os.path.exists(os.path.join(DATA_DIR, "running.state"))
            return {
                gr_hunting_start_button: gr.Button(visible=not is_hunting),
                gr_hunting_stop_button: gr.Button(visible=is_hunting),
                gr_hunting_logs: gr.Textbox(visible=is_hunting),
            }


        # GPU 和地区
        gr_gpu_types.change(load_region_options, [gr_gpu_types], outputs=[gr_regions])
        gr_is_token_ready.change(load_gpu_region_options, None, [gr_gpu_types, gr_regions])

        # 镜像选择
        gr_image_category.change(
            load_image_options,
            [gr_image_category],
            [
                gr_base_image_group, gr_shared_image_group, gr_private_image_group,
                gr_base_image, gr_shared_image, gr_private_image
            ]
        )

        gr_shared_image_search.change(load_shared_image_options, [gr_shared_image_search], [gr_shared_image])

        # 扩展磁盘
        gr_expand_disk_gb.change(
            update_disk_accordion,
            [gr_expand_disk_gb],
            [gr_expand_disk_accordion],
            show_progress=False
        )

        gr_is_token_ready.change(update_disk_accordion, [gr_expand_disk_gb], [gr_expand_disk_accordion])

        # 克隆现有实例
        gr_clone_instances_refresh_button.click(
            load_clone_instances_options, None,
            [gr_clone_instances]
        )
        gr_clone_instances.change(
            update_clone_instances_accordion,
            [gr_clone_instances],
            [gr_clone_instances_accordion],
            show_progress="hidden"
        )
        gr_is_token_ready.change(update_clone_instances_accordion, [gr_clone_instances], [gr_clone_instances_accordion])
        gr_is_token_ready.change(
            lambda ready: load_clone_instances_options() if ready else {
                gr_clone_instances: gr.Dropdown(choices=[], value=[], multiselect=True)
            },
            [gr_is_token_ready], [gr_clone_instances]
        )

        # 定时关机
        gr_is_token_ready.change(update_shutdown_time_accordion, [gr_shutdown_time_type], [gr_shutdown_time_accordion])
        gr_shutdown_time_type.change(
            update_shutdown_time_accordion,
            [gr_shutdown_time_type],
            [gr_shutdown_time_accordion],
            show_progress=False,
        )

        # 邮件通知
        gr_email_notify_sender.change(
            update_email_notify_accordion,
            [gr_email_notify_sender],
            [gr_email_notify_accordion],
            show_progress="hidden",
        )
        gr_is_token_ready.change(update_email_notify_accordion, [gr_email_notify_sender], [gr_email_notify_accordion])
        gr_email_notify_send_button.click(
            send_test_email,
            [gr_email_notify_sender, gr_email_notify_smtp_password, gr_email_notify_smtp_server],
            [gr_email_notify_accordion, gr_email_notify_send_output],
        )

        # 开始
        hunting_job = gr_hunting_start_button.click(
            hunting_start,
            [
                gr_gpu_types,
                gr_regions,
                gr_gpu_num,
                gr_instance_num,
                gr_image_category,
                gr_base_image,
                gr_shared_image,
                gr_private_image,
                gr_expand_disk_gb,
                gr_clone_instances,
                gr_copy_data_after_clone,
                gr_keep_address_after_clone,
                gr_shutdown_time_type,
                gr_email_notify_sender,
                gr_email_notify_smtp_password,
                gr_email_notify_smtp_server,
                gr_scan_interval,
                gr_shutdown_hunter_after_success,
            ],
            [
                gr_hunting_start_button,
                gr_hunting_error,
                gr_hunting_stop_button,
                gr_hunting_logs,
            ]
        )

        gr_hunting_stop_button.click(
            hunting_stop,
            None,
            [
                gr_hunting_start_button,
                gr_hunting_error,
                gr_hunting_stop_button,
                gr_hunting_logs,
            ],
            cancels=[hunting_job],
        )

        gr_is_token_ready.change(
            check_hunting_status,
            None,
            [
                gr_hunting_start_button,
                gr_hunting_stop_button,
                gr_hunting_logs,
            ]
        )

        # 日志
        demo.load(read_output_logs, None, gr_hunting_logs, every=1)

    with gr.Tab("🐰 算力实况", visible=False) as gr_stat_tab:
        gr.Markdown("## 当前 GPU 主机数量")
        gr_gpu_checkbox_group = gr.CheckboxGroup([], label="显卡型号")
        gr_gpu_region_matrix = gr.Matrix()
        with gr.Row():
            with gr.Column(scale=8):
                gr_stat_note = gr.Markdown()
            with gr.Column(scale=2):
                gr_stat_refresh_button = gr.Button("立即更新", size="sm")


        def refresh_stat(gpu_type_names=None):
            region_list = RegionList().update()
            return load_stat(gpu_type_names, region_list)


        def load_stat(gpu_type_names=None, region_list=None):
            region_list = region_list or RegionList().load()
            gpu_type_names = gpu_type_names or [
                "RTX 4090",
                "RTX 3090",
                "RTX 3080 Ti",
                "RTX 3080",
                "RTX 3060",
            ]
            return {
                **update_matrix(gpu_type_names),
                gr_gpu_checkbox_group: gr.CheckboxGroup(choices=region_list.get_gpu_type_names(), value=gpu_type_names),
                gr_stat_note: gr.Markdown(
                    f"以上是当前 AutoDL 官网查询到的 GPU 主机数量，更新时间："
                    f'{region_list.modified_time.strftime("%Y-%m-%d %H:%M:%S") if region_list.modified_time else "N/A"}。'
                ),
            }


        def update_matrix(gpu_type_names):
            region_list = RegionList().load()
            return {
                gr_gpu_region_matrix: gr.Matrix(
                    headers=["地区"] + [n for n in gpu_type_names],
                    value=[
                        [r["region_name"]] + [
                            region_list.get_region_stats([gn], [r["region_name"]])[0]["idle_gpu_num"] or ""
                            for gn in gpu_type_names
                        ]
                        for r in region_list.get_region_stats()
                    ]
                ),
            }


        gr_gpu_checkbox_group.change(
            update_matrix,
            inputs=[gr_gpu_checkbox_group],
            outputs=[gr_gpu_region_matrix],
            show_progress="hidden"
        )
        gr_stat_refresh_button.click(
            refresh_stat,
            inputs=[gr_gpu_checkbox_group],
            outputs=[gr_gpu_checkbox_group, gr_gpu_region_matrix, gr_stat_note]
        )
        gr_is_token_ready.change(load_stat, outputs=[gr_gpu_checkbox_group, gr_gpu_region_matrix, gr_stat_note])


    def login_error(message):
        return {
            gr_is_token_ready: gr.Radio(value=False),
            gr_token_input_group: gr.Group(visible=True),
            gr_token_view_group: gr.Group(visible=False),
            gr_token_input: gr.Textbox(value=""),
            gr_token_input_error: gr.Markdown(visible=bool(message), value=message),
            gr_config_tab: gr.Tab(visible=False),
            gr_stat_tab: gr.Tab(visible=False),
        }


    def load_config(config=None, region_list=None):
        if config is None:
            config = Config().load()
        if config.web_token:
            try:
                if region_list is None:
                    RegionList().update()
            except FailedError as e:
                return login_error(str(e))
            except RequestException:
                return login_error("无法连接 AutoDL，请检查网络或代理后重试。")
            return {
                gr_is_token_ready: gr.Radio(value=True),
                gr_token_input_group: gr.Group(visible=False),
                gr_token_view_group: gr.Group(visible=True),
                gr_token_input: gr.Textbox(value=""),
                gr_token_view_input: "普通实例网页登录已验证",
                gr_config_tab: gr.Tab(visible=True),
                gr_stat_tab: gr.Tab(visible=True),
                gr_token_input_error: gr.Markdown(visible=False),

                gr_gpu_types: gr.CheckboxGroup(value=config.gpu_type_names),
                gr_regions: gr.CheckboxGroup(value=config.region_names),
                gr_gpu_num: gr.Radio(value=config.gpu_idle_num),
                gr_instance_num: gr.Slider(value=config.instance_num),
                gr_image_category: gr.Radio(value=config.image_category),

                gr_base_image: gr.Dropdown(
                    value=json_dumps({"base_image_uuid": config.base_image_uuid}) if config.base_image_uuid else json_dumps({
                        "base_image_labels": config.base_image_labels,
                    }) if config.base_image_labels else None,
                    allow_custom_value=True,
                ),
                gr_shared_image_search: gr.Textbox(value=config.shared_image_keyword),
                gr_shared_image: gr.Dropdown(
                    value=json_dumps({
                        "shared_image_keyword": config.shared_image_keyword,
                        "shared_image_username_keyword": config.shared_image_username_keyword,
                        "shared_image_version": config.shared_image_version,
                    }) if config.shared_image_keyword else None,
                    allow_custom_value=True,
                ),
                gr_private_image: gr.Dropdown(
                    value=json_dumps({
                        "private_image_uuid": config.private_image_uuid,
                        "private_image_name": config.private_image_name,
                    }) if config.private_image_uuid else None,
                    allow_custom_value=True,
                ),
                gr_expand_disk_gb: gr.Slider(value=config.expand_data_disk / 1024 / 1024 / 1024),
                gr_clone_instances: gr.Dropdown(
                    value=[json_dumps(i) for i in config.clone_instances]
                ),
                gr_copy_data_after_clone: gr.Checkbox(value=config.copy_data_disk_after_clone),
                gr_keep_address_after_clone: gr.Checkbox(value=config.keep_src_user_service_address_after_clone),
                gr_shutdown_time_type: gr.Radio(
                    value="今晚 23:59" if config.shutdown_instance_today
                    else f"{config.shutdown_instance_after_hours} 小时" if config.shutdown_instance_after_hours > 0
                    else "不关机"
                ),
                gr_email_notify_sender: gr.Textbox(value=config.mail_sender),
                gr_email_notify_smtp_password: gr.Textbox(value=config.mail_smtp_password),
                gr_email_notify_smtp_server: gr.Textbox(
                    value=f"{config.mail_smtp_host}:{config.mail_smtp_port}" if config.mail_smtp_host else ""
                ),
                gr_scan_interval: gr.Slider(value=config.retry_interval_seconds),
                gr_shutdown_hunter_after_success: gr.Radio(value=config.shutdown_hunter_after_finished),
            }
        else:
            return login_error(
                "检测到之前保存的开发者 Token。请按上方步骤填写网页登录凭据，原开发者 Token 会保留。"
                if config.token else ""
            )


    def save_token(token):
        config = Config().load()
        token = token.strip()
        if not token:
            return login_error("请粘贴网页登录凭据。")
        if token == config.token.strip():
            return login_error("这与之前保存的开发者 Token 相同。请复制官网请求头中的 Authorization 值。")
        if any(character.isspace() for character in token) or not token.isascii():
            return login_error("凭据格式不正确。请只复制 Authorization 的值，不要包含字段名、引号、空格或换行。")
        candidate_client = AutodlClient(token=token)
        region_list = RegionList()
        try:
            region_list.fetch(client=candidate_client)
            # Verify the ordinary-instance endpoint without creating or changing anything.
            candidate_client.request("/api/v1/instance", body={"page_index": 1, "page_size": 1, "status": []})
        except FailedError as e:
            return login_error(str(e))
        except RequestException:
            return login_error("无法连接 AutoDL，请检查网络或代理后重试；新凭据尚未保存。")
        config.web_token = token
        config.save()
        autodl_client.load_config()
        region_list.save()
        return load_config(config, region_list=region_list)


    def clear_token():
        config = Config().load()
        config.web_token = ""
        config.save()
        autodl_client.load_config()
        return login_error("")


    load_config_outputs = [
        gr_is_token_ready,
        gr_token_input_group,
        gr_token_input,
        gr_token_input_error,
        gr_token_view_group,
        gr_token_view_input,
        gr_config_tab,
        gr_stat_tab,
        gr_gpu_types,
        gr_regions,
        gr_gpu_num,
        gr_instance_num,
        gr_image_category,
        gr_base_image,
        gr_shared_image_search,
        gr_shared_image,
        gr_private_image,
        gr_expand_disk_gb,
        gr_clone_instances,
        gr_copy_data_after_clone,
        gr_keep_address_after_clone,
        gr_shutdown_time_type,
        gr_email_notify_sender,
        gr_email_notify_smtp_password,
        gr_email_notify_smtp_server,
        gr_scan_interval,
        gr_shutdown_hunter_after_success,
    ]
    demo.load(load_config, None, load_config_outputs)
    gr_token_save_button.click(save_token, [gr_token_input], load_config_outputs)
    gr_token_clear_button.click(clear_token, None, load_config_outputs)

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", share=False)
