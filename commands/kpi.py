"""`/kpi` 子命令实现。"""

from __future__ import annotations

import asyncio
import datetime
import os
import shutil
from collections.abc import AsyncIterator
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain

from ..core.diary_runtime import handle_complete_command, handle_diary_command
from ..core.learning_preferences import add_user_preference, learn_from_correction
from ..core.persona import get_end_response, get_persona_prompt, get_start_response
from .kpi_doctor import build_kpi_doctor_report


async def render_preset_list(sc: Any, event: AstrMessageEvent) -> AsyncIterator[Any]:
    if not sc.parsed_custom_presets:
        yield event.plain_result(
            "当前还没有自定义预设。\n"
            "用法: /kpi y [预设序号] [间隔秒数] [触发概率]\n"
            "例如: /kpi y 1 90 30"
        )
        return

    msg = "当前自定义预设：\n"
    for i, preset in enumerate(sc.parsed_custom_presets):
        current_marker = ""
        if i == sc.current_preset_index:
            current_marker = " <- 当前使用"
        msg += (
            f"{i}. {preset['name']}: {preset['check_interval']} 秒间隔，"
            f"{preset['trigger_probability']}% 触发概率{current_marker}\n"
        )

    msg += f"\n当前使用: {'预设 ' + str(sc.current_preset_index) if sc.current_preset_index >= 0 else '手动配置'}"
    msg += "\n切换预设: /kpi [预设序号]，例如 /kpi 0"
    yield event.plain_result(msg)


async def render_webui_status(sc: Any, event: AstrMessageEvent) -> AsyncIterator[Any]:
    sc._ensure_runtime_state()
    if sc.webui_enabled:
        webui_running = sc.web_server is not None and getattr(sc.web_server, "_started", False)

        if webui_running:
            actual_port = getattr(sc.web_server, "port", sc.webui_port)
            bind_host = sc.webui_host
            if bind_host == "0.0.0.0":
                access_url = f"http://127.0.0.1:{actual_port}"
            else:
                access_url = f"http://{bind_host}:{actual_port}"

            auth_status = "已启用" if sc.webui_auth_enabled else "未启用"
            password = sc.webui_password or "（未设置，首次访问时会自动生成）"

            response = "WebUI 状态：已启用\n"
            response += f"访问地址：{access_url}\n"
            response += f"认证状态：{auth_status}\n"
            response += f"访问密码：{password}\n"
            response += f"会话超时：{sc.webui_session_timeout} 秒"
        else:
            try:
                await sc._start_webui()
                webui_running = sc.web_server is not None and getattr(sc.web_server, "_started", False)
                if webui_running:
                    actual_port = getattr(sc.web_server, "port", sc.webui_port)
                    bind_host = sc.webui_host
                    if bind_host == "0.0.0.0":
                        access_url = f"http://127.0.0.1:{actual_port}"
                    else:
                        access_url = f"http://{bind_host}:{actual_port}"

                    auth_status = "已启用" if sc.webui_auth_enabled else "未启用"
                    password = sc.webui_password or "（未设置，首次访问时会自动生成）"

                    response = "WebUI 状态：已启用\n"
                    response += f"访问地址：{access_url}\n"
                    response += f"认证状态：{auth_status}\n"
                    response += f"访问密码：{password}\n"
                    response += f"会话超时：{sc.webui_session_timeout} 秒"
                else:
                    response = "WebUI 已启用但启动失败，请检查配置和端口占用情况。\n"
                    response += f"配置的端口：{sc.webui_port}\n"
                    response += f"配置的地址：{sc.webui_host}"
            except Exception as e:
                response = f"WebUI 已启用但启动失败：{str(e)}"
    else:
        response = "WebUI 未启用，请在配置中开启。"

    yield event.plain_result(response)


async def render_status_report(sc: Any, event: AstrMessageEvent) -> AsyncIterator[Any]:
    report = await build_kpi_doctor_report(sc, event)
    yield event.plain_result(report)


async def run_kpi_ys(
    sc: Any, event: AstrMessageEvent, preset_index: int | None = None
) -> AsyncIterator[Any]:
    if preset_index is None:
        async for result in render_preset_list(sc, event):
            yield result
        return

    if preset_index < 0:
        sc.current_preset_index = -1
        sc.plugin_config.current_preset_index = -1
        yield event.plain_result("已切换到手动配置模式。")
        return

    if preset_index >= len(sc.parsed_custom_presets):
        yield event.plain_result(
            f"预设 {preset_index} 不存在。\n"
            f"当前共有 {len(sc.parsed_custom_presets)} 个预设。\n"
            f"用法: /kpi y [序号] [间隔秒数] [触发概率]"
        )
        return

    sc.current_preset_index = preset_index
    sc.plugin_config.current_preset_index = preset_index

    preset = sc.parsed_custom_presets[preset_index]
    yield event.plain_result(
        f"已切换到预设 {preset_index}: {preset['name']}，间隔 {preset['check_interval']} 秒，"
        f"触发概率 {preset['trigger_probability']}%"
    )


async def run_kpi_start(sc: Any, event: AstrMessageEvent) -> AsyncIterator[Any]:
    sc._ensure_runtime_state()
    if not sc.enabled:
        yield event.plain_result("插件当前未启用，请先在配置中开启后再启动自动观察。")
        return

    ok, err_msg = sc._check_env(check_mic=False)
    if not ok:
        yield event.plain_result(f"启动失败：\n{err_msg}")
        return

    if sc.AUTO_TASK_ID in sc.auto_tasks:
        logger.info("自动观察任务已存在，无需重复启动")
        return

    sc.state = "active"
    sc.is_running = True
    logger.info(f"启动任务 {sc.AUTO_TASK_ID}")
    sc.auto_tasks[sc.AUTO_TASK_ID] = asyncio.create_task(
        sc._auto_screen_task(event, task_id=sc.AUTO_TASK_ID)
    )
    start_response = await get_start_response(sc, event.unified_msg_origin)
    yield event.plain_result(f"已启动自动观察任务 {sc.AUTO_TASK_ID}。\n{start_response}")


async def run_kpi_stop(
    sc: Any, event: AstrMessageEvent, task_id: str | None = None
) -> AsyncIterator[Any]:
    sc._ensure_runtime_state()
    if task_id:
        if task_id in sc.auto_tasks:
            task = sc.auto_tasks.pop(task_id)
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"等待任务 {task_id} 停止超时")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"停止任务 {task_id} 失败: {e}")
            yield event.plain_result(f"已停止自动观察任务 {task_id}。")
        else:
            yield event.plain_result(f"任务 {task_id} 不存在。")
    else:
        tasks_to_cancel = list(sc.auto_tasks.items())
        for tid, task in tasks_to_cancel:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"等待任务 {tid} 停止超时")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"停止任务 {tid} 失败: {e}")
            sc.auto_tasks.pop(tid, None)

        if hasattr(sc, "window_companion_active_title") and sc.window_companion_active_title:
            await sc._stop_window_companion_session(reason="manual_stop")

        sc.is_running = False
        sc.state = "inactive"
        end_response = await get_end_response(sc, event.unified_msg_origin)
        yield event.plain_result(f"已停止所有自动观察任务。\n{end_response}")


async def run_kpi_status(sc: Any, event: AstrMessageEvent) -> AsyncIterator[Any]:
    async for result in render_status_report(sc, event):
        yield result


async def run_kpi_list(sc: Any, event: AstrMessageEvent) -> AsyncIterator[Any]:
    sc._ensure_runtime_state()
    if not sc.auto_tasks:
        yield event.plain_result("当前没有运行中的自动观察任务。")
    else:
        msg = "当前运行中的任务：\n"
        for tid in sc.auto_tasks:
            msg += f"- {tid}\n"
        yield event.plain_result(msg)


async def run_kpi_ffmpeg(
    sc: Any, event: AstrMessageEvent, ffmpeg_path: str | None = None
) -> AsyncIterator[Any]:
    if not ffmpeg_path:
        current_ffmpeg = sc._get_ffmpeg_path()
        if current_ffmpeg:
            yield event.plain_result(f"当前 ffmpeg 路径：{current_ffmpeg}")
        else:
            storage_dir = sc._get_ffmpeg_storage_dir()
            yield event.plain_result(
                "未找到 ffmpeg。\n"
                "用法: /kpi ffmpeg [ffmpeg.exe 所在路径]\n"
                "例如: /kpi ffmpeg C:\\Users\\用户名\\Downloads\\ffmpeg\\bin\\ffmpeg.exe\n"
                "\n"
                f"插件会自动将 ffmpeg 复制到插件数据目录的 bin 文件夹：{storage_dir}"
            )
        return

    source_path = os.path.abspath(os.path.expanduser(ffmpeg_path.strip()))

    ffmpeg_bin_dir = sc._get_ffmpeg_storage_dir(create=True)

    dest_path = os.path.join(ffmpeg_bin_dir, "ffmpeg.exe")

    if not os.path.exists(source_path):
        yield event.plain_result(f"源文件不存在：{source_path}")
        return

    try:
        shutil.copy2(source_path, dest_path)
        sc._recording_ffmpeg_path = None
        new_path = sc._get_ffmpeg_path()
        yield event.plain_result(f"ffmpeg 已复制到：{new_path}")
    except Exception as e:
        yield event.plain_result(f"复制失败：{str(e)}")


async def run_kpi_y(
    sc: Any,
    event: AstrMessageEvent,
    preset_index: int | None = None,
    interval: int | None = None,
    probability: int | None = None,
) -> AsyncIterator[Any]:
    if preset_index is None:
        yield event.plain_result(
            "用法: /kpi y [预设序号] [间隔秒数] [触发概率]\n"
            "例如: /kpi y 1 90 30 表示把预设 1 设置为每 90 秒、30% 概率触发"
        )
        return

    if interval is None or probability is None:
        yield event.plain_result(
            "用法: /kpi y [预设序号] [间隔秒数] [触发概率]\n"
            "例如: /kpi y 1 90 30 表示把预设 1 设置为每 90 秒、30% 概率触发"
        )
        return

    if preset_index < 0:
        yield event.plain_result("预设序号不能小于 0。")
        return

    interval = max(10, int(interval))
    probability = max(0, min(100, int(probability)))

    lines = []
    if sc.custom_presets:
        lines = sc.custom_presets.strip().split("\n")

    preset_name = f"预设{preset_index}"
    new_preset = f"{preset_name}|{interval}|{probability}"

    while len(lines) <= preset_index:
        lines.append("")

    lines[preset_index] = new_preset

    sc.custom_presets = "\n".join(lines)
    sc.plugin_config.custom_presets = sc.custom_presets

    sc._parse_custom_presets()

    yield event.plain_result(
        f"已更新预设 {preset_index}：间隔 {interval} 秒，触发概率 {probability}%"
    )


async def run_kpi_p(sc: Any, event: AstrMessageEvent) -> AsyncIterator[Any]:
    async for result in render_preset_list(sc, event):
        yield result


async def run_kpi_add(
    sc: Any, event: AstrMessageEvent, interval: int, *prompt: str
) -> AsyncIterator[Any]:
    if not sc.enabled:
        yield event.plain_result("插件当前未启用，请先开启后再添加自定义任务。")
        return

    custom_prompt = " ".join(prompt) if prompt else ""
    try:
        interval = max(30, int(interval))
        if not sc.is_running:
            sc.is_running = True
        task_id = f"task_{sc.task_counter}"
        sc.task_counter += 1
        sc.auto_tasks[task_id] = asyncio.create_task(
            sc._auto_screen_task(
                event,
                task_id=task_id,
                custom_prompt=custom_prompt,
                interval=interval,
            )
        )
        yield event.plain_result(f"已添加自定义任务 {task_id}，触发间隔为 {interval} 秒。")
    except ValueError:
        yield event.plain_result("用法: /kpi add [间隔秒数] [自定义提示词]")


async def run_kpi_d(
    sc: Any, event: AstrMessageEvent, date: str | None = None
) -> AsyncIterator[Any]:
    async for result in handle_diary_command(sc, event, date):
        yield result


async def run_kpi_correct(sc: Any, event: AstrMessageEvent, *args: Any) -> AsyncIterator[Any]:
    if len(args) < 2:
        yield event.plain_result("用法: /kpi correct [原回复] [纠正后的回复]")
        return

    original = args[0]
    corrected = " ".join(args[1:])

    learn_from_correction(sc, original, corrected)

    yield event.plain_result("已记录这次纠正，我会把它作为后续参考。")


async def run_kpi_preference(
    sc: Any, event: AstrMessageEvent, category: str, *preference: str
) -> AsyncIterator[Any]:
    if not preference:
        yield event.plain_result("用法: /kpi preference [类别] [偏好内容]")
        yield event.plain_result("支持的类别: music, movies, food, hobbies, other")
        return

    valid_categories = ["music", "movies", "food", "hobbies", "other"]
    if category not in valid_categories:
        yield event.plain_result(f"无效类别，支持的类别有: {', '.join(valid_categories)}")
        return

    preference_content = " ".join(preference)

    add_user_preference(sc, category, preference_content)

    yield event.plain_result(f"已添加偏好: {category} - {preference_content}")


async def run_kpi_recent(sc: Any, event: AstrMessageEvent, days: int = 3) -> AsyncIterator[Any]:
    if not sc.enable_diary:
        yield event.plain_result("日记功能当前未启用。")
        return

    days = max(1, min(7, int(days)))
    today = datetime.date.today()
    found_diaries = []

    for i in range(days):
        target_date = today - datetime.timedelta(days=i)
        diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
        diary_path = os.path.join(sc.diary_storage, diary_filename)

        if os.path.exists(diary_path):
            try:
                with open(diary_path, encoding="utf-8") as f:
                    diary_content = f.read()
                found_diaries.append({"date": target_date, "content": diary_content})
            except Exception as e:
                logger.error(f"读取日记失败: {e}")

    if not found_diaries:
        yield event.plain_result("最近几天还没有找到可查看的日记。")
        return

    if sc.diary_auto_recall:
        logger.info(f"日记消息将在 {sc.diary_recall_time} 秒后自动撤回")

        async def recall_message():
            await asyncio.sleep(sc.diary_recall_time)
            try:
                logger.info(f"最近日记消息已到达自动撤回时间: {sc.diary_recall_time} 秒")
            except Exception as e:
                logger.error(f"自动撤回日记记录失败: {e}")

        task = asyncio.create_task(recall_message())
        sc.background_tasks.append(task)

    for diary in found_diaries:
        summary_start = diary["content"].find("## 今日感想")
        if summary_start != -1:
            summary_content = diary["content"][summary_start:]
            summary_lines = summary_content.split("\n")
            summary_text = sc._extract_diary_preview_text("\n".join(summary_lines[2:]).strip())
            if len(summary_text) > 500:
                summary_text = summary_text[:497] + "..."
            diary_message = (
                f"{sc.bot_name} 的日记\n{diary['date'].strftime('%Y年%m月%d日')}\n\n"
                f"{summary_text or '这篇日记里还没有整理出完整感想。'}"
            )
        else:
            summary_start = diary["content"].find(f"## {sc.bot_name}的总结")
            if summary_start == -1:
                summary_start = diary["content"].find("## 总结")
            if summary_start != -1:
                summary_content = diary["content"][summary_start:]
                summary_lines = summary_content.split("\n")
                summary_text = sc._extract_diary_preview_text("\n".join(summary_lines[2:]).strip())
                if len(summary_text) > 500:
                    summary_text = summary_text[:497] + "..."
                diary_message = (
                    f"{sc.bot_name} 的日记\n{diary['date'].strftime('%Y年%m月%d日')}\n\n"
                    f"{summary_text or '这篇日记里还没有整理出完整感想。'}"
                )
            else:
                diary_text = sc._extract_diary_preview_text(diary["content"])
                if len(diary_text) > 500:
                    diary_text = diary_text[:497] + "..."
                diary_message = (
                    f"{sc.bot_name} 的日记\n{diary['date'].strftime('%Y年%m月%d日')}\n\n"
                    f"{diary_text or '这篇日记里还没有可展示的内容。'}"
                )

        send_as_image = sc.diary_send_as_image

        if send_as_image:
            try:
                temp_file_path = sc._generate_diary_image(diary_message)
                yield event.image_result(temp_file_path)
                os.unlink(temp_file_path)
            except Exception as e:
                logger.error(f"生成日记图片失败: {e}")
                yield event.plain_result(diary_message)
        else:
            yield event.plain_result(diary_message)

        await asyncio.sleep(0.5)

    async def generate_blame():
        provider = sc._as_context().get_using_provider()
        if provider:
            try:
                system_prompt = await get_persona_prompt(sc, event.unified_msg_origin)
                response = await provider.text_chat(
                    prompt=sc.diary_response_prompt, system_prompt=system_prompt
                )
                if (
                    response
                    and hasattr(response, "completion_text")
                    and response.completion_text
                ):
                    await sc._as_context().send_message(
                        event.unified_msg_origin,
                        MessageChain([Plain(response.completion_text)]),
                    )
                else:
                    await sc._as_context().send_message(
                        event.unified_msg_origin,
                        MessageChain([Plain("喂，你怎么一下子翻了我这么多天的日记呀，真是的……")]),
                    )
            except Exception as e:
                logger.error(f"生成日记被偷看回复失败: {e}")
                await sc._as_context().send_message(
                    event.unified_msg_origin,
                    MessageChain([Plain("喂，你怎么一下子翻了我这么多天的日记呀，真是的……")]),
                )
        else:
            await sc._as_context().send_message(
                event.unified_msg_origin,
                MessageChain([Plain("喂，你怎么一下子翻了我这么多天的日记呀，真是的……")]),
            )

    blame_task = asyncio.create_task(generate_blame())
    sc.background_tasks.append(blame_task)


async def run_kpi_debug(
    sc: Any, event: AstrMessageEvent, status: str | None = None
) -> AsyncIterator[Any]:
    if status is None:
        current_status = sc.debug
        status_text = "开启" if current_status else "关闭"
        yield event.plain_result(f"当前调试模式状态：{status_text}")
        return

    status = status.lower()
    if status == "on":
        sc.plugin_config.debug = True
        yield event.plain_result("调试模式已开启，后续会输出更多日志。")
    elif status == "off":
        sc.plugin_config.debug = False
        yield event.plain_result("调试模式已关闭，将隐藏大部分调试日志。")
    else:
        yield event.plain_result("用法: /kpi debug [on/off]")


async def run_kpi_webui(sc: Any, event: AstrMessageEvent, action: str = "") -> AsyncIterator[Any]:
    action_text = str(action or "").strip().lower()
    if not action_text:
        async for result in render_webui_status(sc, event):
            yield result
        return
    if action_text == "start":
        if sc.web_server:
            yield event.plain_result("WebUI 已经在运行中。")
        else:
            await sc._start_webui()
            yield event.plain_result(f"WebUI 已启动，访问地址: http://127.0.0.1:{sc.webui_port}")
    elif action_text == "stop":
        if not sc.web_server:
            yield event.plain_result("WebUI 当前没有运行。")
        else:
            await sc._stop_webui()
            sc.web_server = None
            yield event.plain_result("WebUI 已停止。")
    else:
        yield event.plain_result("无效操作，请使用 /kpi webui start 或 /kpi webui stop")


async def run_kpi_cd(
    sc: Any, event: AstrMessageEvent, date: str | None = None
) -> AsyncIterator[Any]:
    async for result in handle_complete_command(sc, event, date):
        yield result
