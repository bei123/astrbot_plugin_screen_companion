"""日记运行时：查看 / 补写、条目累积、定时生成与调度循环。"""

from __future__ import annotations

import asyncio
import datetime
import os
from collections.abc import AsyncIterator
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain

from .diary import render_diary_message_to_png
from .persona import get_persona_prompt


def add_diary_entry(host: Any, content: str, active_window: str) -> bool:
    """添加日记条目。"""
    if not host.enable_diary:
        return False

    should_store, reason = host._should_store_diary_entry(content, active_window)
    if not should_store:
        logger.info(f"跳过写入日记条目: {reason}")
        return False

    now = datetime.datetime.now()
    entry = {
        "time": now.strftime("%H:%M:%S"),
        "content": content,
        "active_window": active_window,
    }
    host.diary_entries.append(entry)
    if len(host.diary_entries) > 10:
        host.diary_entries = host.diary_entries[-10:]
    logger.info(f"添加日记条目: {entry}")
    return True


async def generate_diary(host: Any, target_date: datetime.date | None = None) -> None:
    """生成日记。"""
    if not host.enable_diary or not host.diary_entries:
        return

    target_date = target_date or datetime.date.today()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays[target_date.weekday()]

    weather_info = ""
    try:
        weather_info = await host._get_weather_prompt()
    except Exception as e:
        logger.debug(f"获取天气信息失败: {e}")

    compacted_entries = host._compact_diary_entries(host.diary_entries)
    if not compacted_entries:
        logger.info("今日日记没有可用的高质量观察条目，已跳过生成")
        provider = host._as_context().get_using_provider()
        if provider:
            try:
                system_prompt = await get_persona_prompt(host)
                weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
                weekday = weekdays[target_date.weekday()]
                weather_info = ""
                try:
                    weather_info = await host._get_weather_prompt()
                except Exception:
                    pass
                no_observation_prompt = (
                    "今天的日记有些特别——你发现用户今天根本没给你看屏幕的机会！\n"
                    "请写一段带点委屈、带点撒娇的日记，抱怨用户今天忽略了你。\n"
                    "可以假装生气、假装吃醋，但最终要表达'明天也要来看你哦'的期待。\n"
                    "字数控制在 150-280 字，自然俏皮一点。\n"
                )
                response = await provider.text_chat(
                    prompt=no_observation_prompt, system_prompt=system_prompt
                )
                if response and hasattr(response, "completion_text") and response.completion_text:
                    reflection_text = host._ensure_diary_reflection_text(
                        response.completion_text,
                        "（今天用户没给我看屏幕的机会，呜呜）",
                    )
                    diary_content = host._build_diary_document(
                        target_date=target_date,
                        weekday=weekday,
                        weather_info=weather_info,
                        observation_text="（今天用户没给我看屏幕的机会，呜呜）",
                        reflection_text=reflection_text,
                    )
                    diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
                    diary_path = os.path.join(host.diary_storage, diary_filename)
                    with open(diary_path, "w", encoding="utf-8") as f:
                        f.write(diary_content)
                    logger.info(f"日记已保存（无观察）: {diary_path}")
            except Exception as e:
                logger.error(f"生成无观察日记失败: {e}")
        host.diary_entries = []
        host.last_diary_date = target_date
        return

    observation_lines = []
    for entry in compacted_entries:
        time_label = (
            entry["start_time"]
            if entry["start_time"] == entry["end_time"]
            else f"{entry['start_time']}-{entry['end_time']}"
        )
        observation_lines.append(f"### {time_label} - {entry['active_window']}")
        if len(entry["points"]) == 1:
            observation_lines.append(entry["points"][0])
        else:
            for point in entry["points"]:
                observation_lines.append(f"- {point}")
        observation_lines.append("")
    observation_text = "\n".join(observation_lines).strip()
    reflection_text = ""

    viewed_count = 0
    for i in range(1, 4):
        past_date = target_date - datetime.timedelta(days=i)
        past_date_str = past_date.strftime("%Y%m%d")
        if past_date_str in host.diary_metadata and host.diary_metadata[past_date_str].get(
            "viewed", False
        ):
            viewed_count += 1

    logger.info(f"最近三天日记查看次数: {viewed_count}")

    provider = host._as_context().get_using_provider()
    if provider:
        if len(compacted_entries) < 2:
            summary_prompt = (
                "今天的观察还比较少，请写一段简短、自然、不过度脑补的今日日记。"
                "可以更克制一点，但仍然要保留一点真实感和陪伴感。"
                "字数控制在 180 到 320 字。"
            )
        else:
            reference_days = []
            if host.diary_reference_days > 0:
                for i in range(1, host.diary_reference_days + 1):
                    past_date = target_date - datetime.timedelta(days=i)
                    past_diary_filename = f"diary_{past_date.strftime('%Y%m%d')}.md"
                    past_diary_path = os.path.join(host.diary_storage, past_diary_filename)
                    if os.path.exists(past_diary_path):
                        try:
                            with open(past_diary_path, encoding="utf-8") as f:
                                past_diary_content = f.read()
                            reference_days.append(
                                {
                                    "date": past_date.strftime("%Y-%m-%d"),
                                    "content": past_diary_content,
                                }
                            )
                        except Exception as e:
                            logger.error(f"读取前几天日记失败: {e}")

            summary_prompt = host._build_diary_reflection_prompt(
                observation_text=observation_text,
                viewed_count=viewed_count,
                reference_days=reference_days,
            )

        try:
            system_prompt = await get_persona_prompt(host)
            response = await provider.text_chat(
                prompt=summary_prompt, system_prompt=system_prompt
            )
            if (
                response
                and hasattr(response, "completion_text")
                and response.completion_text
            ):
                reflection_text = host._ensure_diary_reflection_text(
                    response.completion_text,
                    observation_text,
                )
        except Exception as e:
            logger.error(f"生成日记总结失败: {e}")

    structured_summary = host._build_diary_structured_summary(
        compacted_entries,
        reflection_text,
    )
    reflection_text = host._ensure_diary_reflection_text(
        reflection_text,
        observation_text,
        structured_summary,
    )
    if not structured_summary.get("suggestion_items"):
        structured_summary["suggestion_items"] = host._extract_actionable_suggestions(
            reflection_text,
            limit=3,
        )

    diary_content = host._build_diary_document(
        target_date=target_date,
        weekday=weekday,
        weather_info=weather_info,
        observation_text=observation_text,
        reflection_text=reflection_text,
        structured_summary=structured_summary,
    )

    diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
    diary_path = os.path.join(host.diary_storage, diary_filename)

    try:
        with open(diary_path, "w", encoding="utf-8") as f:
            f.write(diary_content)
        host._save_diary_structured_summary(target_date, structured_summary)
        host._remember_diary_summary_memories(target_date, structured_summary)
        host._update_memory_priorities()
        host._save_long_term_memory()
        logger.info(f"日记已保存到: {diary_path}")

        host.diary_entries = []
        host.last_diary_date = target_date

        logger.info("日记生成完成，不自动发送，等待用户主动查看")
    except Exception as e:
        logger.error(f"保存日记失败: {e}")


async def handle_diary_command(
    host: Any,
    event: AstrMessageEvent,
    date: str | None = None,
) -> AsyncIterator[Any]:
    """处理日记查看命令。"""
    if not host.enable_diary:
        yield event.plain_result("补写日记失败了，这次没有成功保存。")
        return

    if date:
        try:
            date_str = str(date)
            if len(date_str) == 8 and date_str.isdigit():
                target_date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
            else:
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            yield event.plain_result(
                "日期格式错误，请使用 YYYY-MM-DD 或 YYYYMMDD，例如：/kpi d 20260302"
            )
            return
    else:
        now = datetime.datetime.now()
        target_date = host._resolve_diary_target_date(now)
        if now.hour < 2:
            yield event.plain_result(
                f"当前时间还在凌晨两点前，默认查看 {target_date.strftime('%Y年%m月%d日')} 的日记。"
            )

    diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
    diary_path = os.path.join(host.diary_storage, diary_filename)

    if not os.path.exists(diary_path):
        yield event.plain_result(
            f"{target_date.strftime('%Y年%m月%d日')} 的日记还不存在。"
        )
        return

    try:
        with open(diary_path, encoding="utf-8") as f:
            diary_content = f.read()

        date_str = target_date.strftime("%Y%m%d")
        host._update_diary_view_status(date_str)

        summary_start = diary_content.find("## 今日感想")
        if summary_start != -1:
            summary_content = diary_content[summary_start:]
            summary_lines = summary_content.split("\n")
            start_idx = 2
            while start_idx < len(summary_lines) and (
                summary_lines[start_idx].strip().startswith("#")
                or not summary_lines[start_idx].strip()
            ):
                start_idx += 1
            summary_text = "\n".join(summary_lines[start_idx:]).strip()
            summary_text = host._extract_diary_preview_text(summary_text)
            if len(summary_text) > 500:
                summary_text = summary_text[:497] + "..."
            diary_message = (
                f"{host.bot_name} 的日记\n{target_date.strftime('%Y年%m月%d日')}\n\n"
                f"{summary_text or '这篇日记里还没有整理出完整感想。'}"
            )
        else:
            summary_start = diary_content.find(f"## {host.bot_name}的总结")
            if summary_start == -1:
                summary_start = diary_content.find("## 总结")
            if summary_start != -1:
                summary_content = diary_content[summary_start:]
                summary_lines = summary_content.split("\n")
                summary_text = host._extract_diary_preview_text(
                    "\n".join(summary_lines[2:]).strip()
                )
                if len(summary_text) > 500:
                    summary_text = summary_text[:497] + "..."
                diary_message = (
                    f"{host.bot_name} 的日记\n{target_date.strftime('%Y年%m月%d日')}\n\n"
                    f"{summary_text or '这篇日记里还没有整理出完整感想。'}"
                )
            else:
                observation_start = diary_content.find("## 今日观察")
                if observation_start != -1:
                    observation_content = diary_content[observation_start:]
                    observation_lines = observation_content.split("\n")
                    observation_text = host._extract_diary_preview_text(
                        "\n".join(observation_lines[2:]).strip()
                    )
                    if len(observation_text) > 500:
                        observation_text = observation_text[:497] + "..."
                    diary_message = (
                        f"{host.bot_name} 的日记\n{target_date.strftime('%Y年%m月%d日')}\n\n"
                        f"{observation_text or '这篇日记里还没有可展示的内容。'}"
                    )
                else:
                    diary_message = (
                        host._extract_diary_preview_text(diary_content)
                        or "这篇日记里还没有可展示的内容。"
                    )

        if host.diary_auto_recall:
            logger.info(f"日记消息将在 {host.diary_recall_time} 秒后自动撤回")

            async def recall_message():
                await asyncio.sleep(host.diary_recall_time)
                try:
                    logger.info(f"日记消息已到达自动撤回时间: {host.diary_recall_time} 秒")
                except Exception as e:
                    logger.error(f"自动撤回日记记录失败: {e}")

            task = asyncio.create_task(recall_message())
            host.background_tasks.append(task)

        send_as_image = host.diary_send_as_image

        if send_as_image:
            try:
                temp_file_path = render_diary_message_to_png(diary_message)
                yield event.image_result(temp_file_path)
                os.unlink(temp_file_path)
            except Exception as e:
                logger.error(f"生成日记图片失败: {e}")
                yield event.plain_result(diary_message)
        else:
            yield event.plain_result(diary_message)

        async def generate_blame():
            provider = host._as_context().get_using_provider()
            if provider:
                try:
                    system_prompt = await get_persona_prompt(
                        host, event.unified_msg_origin
                    )
                    response = await provider.text_chat(
                        prompt=host.diary_response_prompt, system_prompt=system_prompt
                    )
                    if (
                        response
                        and hasattr(response, "completion_text")
                        and response.completion_text
                    ):
                        await host._as_context().send_message(
                            event.unified_msg_origin,
                            MessageChain([Plain(response.completion_text)]),
                        )
                    else:
                        await host._as_context().send_message(
                            event.unified_msg_origin,
                            MessageChain([Plain("喂，你怎么又偷看我的日记呀，真是的……")]),
                        )
                except Exception as e:
                    logger.error(f"生成日记被偷看回复失败: {e}")
                    await host._as_context().send_message(
                        event.unified_msg_origin,
                        MessageChain([Plain("喂，你怎么又偷看我的日记呀，真是的……")]),
                    )
            else:
                await host._as_context().send_message(
                    event.unified_msg_origin,
                    MessageChain([Plain("喂，你怎么又偷看我的日记呀，真是的……")]),
                )

        blame_task = asyncio.create_task(generate_blame())
        host.background_tasks.append(blame_task)

    except Exception as e:
        logger.error(f"读取日记失败: {e}")
        yield event.plain_result("读取这篇日记时出了点问题。")


async def handle_complete_command(
    host: Any,
    event: AstrMessageEvent,
    date: str | None = None,
) -> AsyncIterator[Any]:
    """处理补写日记命令。"""
    if not host.enable_diary:
        yield event.plain_result("当前没有开启日记功能，暂时无法补写。")
        return

    if date:
        try:
            date_str = str(date)
            if len(date_str) == 8 and date_str.isdigit():
                target_date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
            else:
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            yield event.plain_result(
                "日期格式错误，请使用 YYYY-MM-DD 或 YYYYMMDD，例如：/kpi cd 20260302"
            )
            return
    else:
        now = datetime.datetime.now()
        target_date = host._resolve_diary_target_date(now)
        if now.hour < 2:
            yield event.plain_result(
                f"当前时间还在凌晨两点前，默认补写 {target_date.strftime('%Y年%m月%d日')} 的日记。"
            )

    diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
    diary_path = os.path.join(host.diary_storage, diary_filename)

    if os.path.exists(diary_path):
        yield event.plain_result(
            f"{target_date.strftime('%Y年%m月%d日')} 的日记已经存在，无需补写。"
        )
        return

    provider = host._as_context().get_using_provider()
    if not provider:
        yield event.plain_result("当前没有可用的模型提供商，暂时无法补写日记。")
        return

    try:
        umo = None
        if event and hasattr(event, "unified_msg_origin"):
            umo = event.unified_msg_origin
        system_prompt = await get_persona_prompt(host, umo)
        weather_info = ""
        observation_text = ""

        target_date_str = target_date.strftime("%Y-%m-%d")
        day_observations = []
        for obs in host.observations:
            if obs.get("timestamp", "").startswith(target_date_str):
                day_observations.append(obs)

        if day_observations:
            observation_text = "当天观察记录：\n"
            for i, obs in enumerate(day_observations, 1):
                observation_text += (
                    f"{i}. 场景：{obs.get('scene', '未知')} - {obs.get('description', '')}\n"
                )
            observation_text += "\n"

        completion_prompt = (
            f"请补写 {target_date.strftime('%Y年%m月%d日')} 的今日日记。\n"
            "要求：\n"
            "1. 保持和现有日记一致的自然口吻。\n"
            "2. 根据当天观察提炼重点，不要逐条堆叠流水账。\n"
            "3. 如果要给建议，优先给和当天任务直接相关的建议。\n"
            "4. 保留真实感，不要写成空泛鸡汤，也不要重复标题和日期。\n"
            "5. 字数控制在 220 到 420 字。\n"
        )

        if day_observations:
            completion_prompt += "\n当天观察记录：\n"
            for obs in day_observations:
                completion_prompt += f"- {obs.get('scene', '未知')}：{obs.get('description', '')}\n"

        reference_days = []
        for i in range(1, 3):
            past_date = target_date - datetime.timedelta(days=i)
            past_diary_filename = f"diary_{past_date.strftime('%Y%m%d')}.md"
            past_diary_path = os.path.join(host.diary_storage, past_diary_filename)
            if os.path.exists(past_diary_path):
                try:
                    with open(past_diary_path, encoding="utf-8") as f:
                        past_diary_content = f.read()
                    reference_days.append(
                        {
                            "date": past_date.strftime("%Y-%m-%d"),
                            "content": past_diary_content,
                        }
                    )
                except Exception as e:
                    logger.error(f"读取前几天日记失败: {e}")

        if reference_days:
            completion_prompt += "\n可参考前几天的日记语气：\n"
            for day in reference_days:
                completion_prompt += f"\n### {day['date']}\n{str(day['content'])[:500]}\n"

        response = await provider.text_chat(
            prompt=completion_prompt, system_prompt=system_prompt
        )

        if (
            response
            and hasattr(response, "completion_text")
            and response.completion_text
        ):
            weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            weekday = weekdays[target_date.weekday()]

            try:
                weather_info = await host._get_weather_prompt(target_date)
            except Exception as e:
                logger.debug(f"获取天气信息失败: {e}")

            diary_content = host._build_diary_document(
                target_date=target_date,
                weekday=weekday,
                weather_info=weather_info,
                observation_text=observation_text,
                reflection_text=host._ensure_diary_reflection_text(
                    response.completion_text,
                    observation_text,
                ),
            )

            try:
                with open(diary_path, "w", encoding="utf-8") as f:
                    f.write(diary_content)
                logger.info(f"补写日记已保存到: {diary_path}")
                yield event.plain_result(
                    f"已补写并保存 {target_date.strftime('%Y年%m月%d日')} 的日记。"
                )
            except Exception as e:
                logger.error(f"保存补写日记失败: {e}")
                yield event.plain_result("补写成功了，但保存日记时出了点问题。")
        else:
            yield event.plain_result("模型没有返回有效内容，这次补写没有成功。")
    except Exception as e:
        logger.error(f"补写日记失败: {e}")
        yield event.plain_result("补写日记时出了点问题，请稍后再试。")


async def run_diary_scheduler(host: Any) -> None:
    """日记定时任务主循环。"""
    while host.running and host._is_current_process_instance():
        try:
            now = datetime.datetime.now()
            target_date = host._resolve_diary_target_date(now)

            if host.enable_diary and host.last_diary_date != target_date:
                try:
                    hour, minute = map(
                        int,
                        host._normalize_clock_text(host.diary_time, "00:00").split(":"),
                    )
                    if now.hour == hour and now.minute == minute:
                        await generate_diary(host, target_date=target_date)
                except Exception as e:
                    logger.error(f"解析日记时间失败: {e}")

            for _ in range(60):
                if not host.running:
                    break
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"日记任务异常: {e}")
            for _ in range(60):
                if not host.running:
                    break
                await asyncio.sleep(1)
