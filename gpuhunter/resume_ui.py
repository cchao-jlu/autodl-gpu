"""Gradio controls for resuming an existing, stopped instance."""

import gradio as gr
from requests import RequestException

from gpuhunter.autodl_client import FailedError, get_default_client
from gpuhunter.instance_resume import ResumeMonitor, instance_label

resume_monitor = ResumeMonitor()


def stopped_instance_choices():
    client = get_default_client()
    return [
        (instance_label(instance), instance["uuid"])
        for instance in client.list_instance("shutdown")
        if instance.get("status") == "shutdown" and instance.get("charge_type") == "payg"
        and isinstance(instance.get("req_gpu_amount"), int) and instance["req_gpu_amount"] > 0
    ]


def build_resume_tab(demo, is_token_ready):
    with gr.Tab("🔄 蹲守开机", visible=False) as resume_tab:
        gr.Markdown(
            "### 等待原主机空闲，自动开机\n"
            "选择一个已关机的普通按量实例，等待原主机有足够的空闲 GPU 后，自动开机并确认运行状态。"
            "保留原实例及磁盘数据。开机后按原实例计费规则收费；请在官网核对价格和定时关机设置。\n\n"
            "关闭网页后监控会继续，本机和 Hunter 服务需要保持运行；电脑休眠时无法扫描。"
            "本次任务完成后停止监控，服务重启后需手动重新开始。"
        )
        with gr.Row():
            instance = gr.Dropdown(label="已关机实例", choices=[], scale=8)
            refresh = gr.Button("刷新实例列表", scale=2)
        notice = gr.Markdown("点击“刷新实例列表”，选择需要开机的原实例。")
        with gr.Row():
            interval = gr.Slider(10, 600, value=30, step=1, label="扫描间隔（秒）")
            duration = gr.Number(value=120, minimum=1, maximum=1440, precision=0, label="最长等待（分钟）")
        consent = gr.Checkbox(value=False, label="我确认自动开机后会产生实例运行费用")
        with gr.Row():
            start = gr.Button("开始监控并自动开机", variant="primary")
            stop = gr.Button("停止监控", variant="stop", interactive=False)
        error = gr.Markdown(visible=False, elem_classes=["error-message"])
        status = gr.Textbox(label="监控状态", value="尚未开始监控。", interactive=False)
        logs = gr.Textbox(label="开机监控日志", lines=8, interactive=False, autoscroll=True)

        def refresh_instances(ready):
            if not ready:
                return gr.Dropdown(choices=[], value=None), "请先验证网页登录凭据。", False
            try:
                choices = stopped_instance_choices()
                note = (f"找到 {len(choices)} 个已关机的普通按量实例，请按实例 ID 选择。" if choices
                        else "当前没有可监控的已关机普通按量实例。")
                return gr.Dropdown(choices=choices, value=None), note, False
            except FailedError as exc:
                return gr.Dropdown(choices=[], value=None), str(exc), False
            except RequestException:
                return gr.Dropdown(choices=[], value=None), "无法读取实例列表，请检查网络后刷新。", False

        def monitor_status():
            active, message, content = resume_monitor.snapshot()
            return [message, content, gr.Button(interactive=not active), gr.Button(interactive=active),
                    gr.Dropdown(interactive=not active), gr.Slider(interactive=not active),
                    gr.Number(interactive=not active), gr.Button(interactive=not active),
                    gr.Checkbox(interactive=not active)]

        def start_monitor(uuid, seconds, minutes, confirmed):
            try:
                resume_monitor.start(uuid, seconds, minutes, confirmed)
            except ValueError as exc:
                return [gr.Markdown(value=str(exc), visible=True), *monitor_status()]
            return [gr.Markdown(value="", visible=False), *monitor_status()]

        def stop_monitor():
            resume_monitor.stop()
            return monitor_status()

        outputs = [status, logs, start, stop, instance, interval, duration, refresh, consent]
        refresh.click(refresh_instances, [is_token_ready], [instance, notice, consent])
        is_token_ready.change(refresh_instances, [is_token_ready], [instance, notice, consent])
        instance.change(lambda: False, None, [consent], queue=False)
        start.click(start_monitor, [instance, interval, duration, consent], [error, *outputs], queue=False)
        stop.click(stop_monitor, None, outputs, queue=False)
        demo.load(monitor_status, None, outputs, every=2, show_progress="hidden")
    return resume_tab
