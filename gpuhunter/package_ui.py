"""Package controls sharing the existing GPU, region and image selectors."""

import json
from pathlib import Path

import gradio as gr
from requests import RequestException

from gpuhunter.autodl_client import FailedError, resolve_image_info
from gpuhunter.data_object import Config, RegionList
from gpuhunter.package_hunt import PACKAGE_TYPES, PackageMonitor, PackageSpec, integer, money_units
from main import DATA_DIR

package_monitor = PackageMonitor()


def build_spec(mode, duration, per_instance, total, wait_minutes, gpu_types, regions,
               gpu_count, instance_count, image_category, base_image, shared_image,
               private_image, interval):
    if mode not in PACKAGE_TYPES:
        raise ValueError("请选择包日、包周或包月。")
    duration = integer(duration, 1, PACKAGE_TYPES[mode][1], "购买时长")
    count = integer(instance_count, 1, 20, "购买台数")
    gpu_count = integer(gpu_count, 1, 12, "每台 GPU 数量")
    interval = integer(interval, 10, 600, "扫描间隔")
    wait_minutes = integer(wait_minutes, 1, 1440, "最长等待分钟数")
    per_units, total_units = money_units(per_instance), money_units(total)
    if not gpu_types or not regions:
        raise ValueError("请在上方选择显卡型号和地区。")
    selected = {"base": base_image, "shared": shared_image, "private": private_image}.get(image_category)
    if not selected:
        raise ValueError("请在上方选择启动镜像。")
    try:
        fields = json.loads(selected)
        if not isinstance(fields, dict):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("镜像选项无效，请重新选择。") from None
    allowed = {"base_image_uuid", "base_image_labels", "shared_image_keyword", "shared_image_username_keyword",
               "shared_image_version", "private_image_uuid", "private_image_name"}
    if not fields.keys() <= allowed:
        raise ValueError("镜像选项包含不支持的参数，请重新选择。")
    region_list = RegionList().load().list
    signs = sorted({s for r in region_list if r["region_name"] in regions for s in r["region_sign"]})
    spec = PackageSpec(mode, duration, signs, sorted(gpu_types), gpu_count, count,
                       resolve_image_info(**fields), per_units, total_units, interval, wait_minutes).validate()
    return spec, fields


def package_summary(mode, duration, per_instance, total, wait_minutes, gpu_types, regions,
                    gpu_count, instance_count, *_):
    label, _, _ = PACKAGE_TYPES.get(mode, ("套餐", 0, 0))
    unit = {"daily": "天", "weekly": "周", "monthly": "个月"}.get(mode, "期")
    return (f"**购买摘要**： {', '.join(gpu_types or []) or '未选显卡'}，"
            f"{', '.join(regions or []) or '未选地区'}；每台 {gpu_count} 张 GPU，共 {instance_count} 台；"
            f"{label} {duration} {unit}。单台整期不超过 **¥{per_instance}**，"
            f"本次订单合计不超过 **¥{total}**，最长等待 {wait_minutes} 分钟。\n\n"
            "使用上方所选镜像及默认磁盘；不克隆、不设置定时关机、不自动续费。"
            "预算仅限制本次套餐订单，不包括到期后的费用或其他独立计费服务；请在官网核对到期设置。")


def build_package_panel(demo, ready, mode, shared_inputs):
    # shared_inputs: GPU types, regions, GPU count, instance count, image category,
    # base/shared/private image values and scan interval.
    defaults = Config().load().package_options
    with gr.Group(visible=False) as group:
        gr.Markdown("### 包日 / 包周 / 包月自动抢购\n沿用上方显卡、地区、台数和镜像设置；库存与报价按所选套餐重新查询。")
        with gr.Row():
            duration = gr.Dropdown(choices=list(range(1, 13)), value=defaults.get("duration", 1),
                                   label="购买时长（天 / 周 / 月）")
            per_instance = gr.Number(label="单台整期价格上限（元）", value=defaults.get("per_instance", 0), precision=2)
            total = gr.Number(label="任务总预算（元）", value=defaults.get("total", 0), precision=2)
            wait_minutes = gr.Number(label="最长等待（分钟）", value=defaults.get("wait_minutes", 120), precision=0)
        inputs = [mode, duration, per_instance, total, wait_minutes, *shared_inputs]
        summary = gr.Markdown("请填写套餐、时长和预算，核对上方 GPU、地区、台数及镜像。")
        consent = gr.Checkbox(value=False, label="我确认以上配置，允许在预算内自动创建并支付套餐订单")
        with gr.Row():
            start = gr.Button("开始 / 继续套餐抢购", variant="primary")
            stop = gr.Button("停止套餐抢购", variant="stop", interactive=False)
            check = gr.Button("核对已有订单（不购买）")
        error = gr.Markdown(visible=False, elem_classes=["error-message"])
        status = gr.Textbox(label="套餐任务状态", interactive=False,
                            value="尚未开始套餐抢购。重启后请先核对已有订单，再手动继续。")
        logs = gr.Textbox(label="套餐抢购日志", lines=8, interactive=False, autoscroll=True)
        with gr.Accordion("结束旧任务并重新配置", open=False):
            gr.Markdown("停止和重启会保留原任务台数、预算及订单记录。若要改变配置，请先到官网核对支付结果、取消待支付订单，再结束旧任务。结束后新任务会重新计算台数和预算。")
            finish_consent = gr.Checkbox(value=False, label="我已在官网核对所有订单、取消待支付订单，确认结束旧任务")
            finish = gr.Button("归档并结束旧任务")

        def change_mode(value):
            if value not in PACKAGE_TYPES:
                return gr.Group(visible=False), gr.Dropdown(choices=[1], value=1), False
            unit = {"daily": "天", "weekly": "周", "monthly": "个月"}[value]
            return gr.Group(visible=True), gr.Dropdown(choices=list(range(1, PACKAGE_TYPES[value][1] + 1)), value=1,
                                                      label=f"购买时长（{unit}）"), False

        def describe(*values):
            return package_summary(*values), False

        def start_task(is_ready, confirmed, *values):
            try:
                if not is_ready:
                    raise ValueError("请先验证网页登录凭据。")
                if confirmed is not True:
                    raise ValueError("请先核对购买摘要并勾选自动购买确认。")
                if Path(DATA_DIR, "running.state").exists():
                    raise ValueError("请先停止按量蹲守，再开始套餐抢购。")
                spec, fields = build_spec(*values)
                package_monitor.start(spec, confirmed=True)
                # Save only selection defaults; credentials never enter the task journal.
                config = Config().load()
                config.package_options = dict(duration=spec.duration, per_instance=values[2],
                                              total=values[3], wait_minutes=spec.wait_minutes)
                config.region_names = values[6]
                config.gpu_type_names = values[5]
                config.gpu_idle_num = spec.gpu_count
                config.instance_num = spec.instance_count
                config.retry_interval_seconds = spec.interval
                config.image_category = values[9]
                for key, empty in (("base_image_uuid", ""), ("base_image_labels", None),
                                   ("shared_image_keyword", ""), ("shared_image_username_keyword", ""),
                                   ("shared_image_version", ""), ("private_image_uuid", ""), ("private_image_name", "")):
                    setattr(config, key, fields.get(key, empty))
                config.save()
                return gr.Markdown(value="", visible=False), False
            except OSError:
                package_monitor.stop()
                return gr.Markdown(value="本地任务或配置保存失败，已停止后续操作；请核对官网订单后处理。", visible=True), False
            except (ValueError, FailedError) as exc:
                return gr.Markdown(value=str(exc), visible=True), False
            except RequestException:
                return gr.Markdown(value="无法读取镜像或登录信息，请稍后重试。", visible=True), False

        def check_task(is_ready):
            try:
                if not is_ready:
                    raise ValueError("请先验证网页登录凭据。")
                package_monitor.start(read_only=True)
                return gr.Markdown(value="", visible=False)
            except (ValueError, FailedError) as exc:
                return gr.Markdown(value=str(exc), visible=True)

        def finish_task(confirmed):
            try:
                package_monitor.finish_manually(confirmed)
                return gr.Markdown(value="", visible=False), False, False
            except ValueError as exc:
                return gr.Markdown(value=str(exc), visible=True), False, False

        poll_state = gr.State(value={})

        def poll(session_state):
            active, message, content = package_monitor.snapshot()
            state = (active, Path(DATA_DIR, "running.state").exists())
            previous = session_state.get("last")
            session_state["last"] = state
            # Avoid repeatedly updating shared selectors: their existing change handlers
            # load images and refresh choices. Only lock/unlock on a task transition.
            shared_updates = ([gr.update(interactive=not active) for _ in shared_inputs]
                              if active or (previous and previous[0]) else [gr.skip() for _ in shared_inputs])
            if previous == state:
                return [message, content, *[gr.skip() for _ in range(10 + len(shared_inputs))]]
            return [message, content, gr.update(interactive=not active), gr.update(interactive=active),
                    gr.update(interactive=not active), gr.update(interactive=not active),
                    gr.update(interactive=not any(state)),
                    *[gr.update(interactive=not active) for _ in range(5)], *shared_updates]

        mode.input(change_mode, mode, [group, duration, consent], queue=False).then(
            describe, inputs, [summary, consent], queue=False, show_progress="hidden")
        for component in inputs[1:]:
            component.input(describe, inputs, [summary, consent], queue=False, show_progress="hidden")
        start.click(start_task, [ready, consent, *inputs], [error, consent], queue=False)
        stop.click(package_monitor.stop, None, None, queue=False)
        check.click(check_task, ready, error, queue=False)
        finish.click(finish_task, finish_consent, [error, finish_consent, consent], queue=False)
        demo.load(poll, [poll_state], [status, logs, start, stop, check, finish, mode,
                              duration, per_instance, total, wait_minutes, consent, *shared_inputs],
                  every=2, show_progress="hidden")
    return group
