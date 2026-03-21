# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import datetime
import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


class ScreenCompanionCommandSupportMixin:
    async def _render_webui_status(self, event: AstrMessageEvent):
        """查看 WebUI 信息。"""
        self._ensure_runtime_state()
        if self.webui_enabled:
            # 检查 WebUI 服务是否正在运行
            webui_running = self.web_server is not None and getattr(self.web_server, "_started", False)
            
            if webui_running:
                # 获取实际使用的端口
                actual_port = getattr(self.web_server, "port", self.webui_port)
                host = self.webui_host
                if host == "0.0.0.0":
                    access_url = f"http://127.0.0.1:{actual_port}"
                else:
                    access_url = f"http://{host}:{actual_port}"
                
                auth_status = "已启用" if self.webui_auth_enabled else "未启用"
                password = self.webui_password or "（未设置，首次访问时会自动生成）"
                
                response = f"WebUI 状态：已启用\n"
                response += f"访问地址：{access_url}\n"
                response += f"认证状态：{auth_status}\n"
                response += f"访问密码：{password}\n"
                response += f"会话超时：{self.webui_session_timeout} 秒"
            else:
                # WebUI 已启用但服务未运行，尝试启动
                try:
                    await self._start_webui()
                    # 再次检查状态
                    webui_running = self.web_server is not None and getattr(self.web_server, "_started", False)
                    if webui_running:
                        actual_port = getattr(self.web_server, "port", self.webui_port)
                        host = self.webui_host
                        if host == "0.0.0.0":
                            access_url = f"http://127.0.0.1:{actual_port}"
                        else:
                            access_url = f"http://{host}:{actual_port}"
                        
                        auth_status = "已启用" if self.webui_auth_enabled else "未启用"
                        password = self.webui_password or "（未设置，首次访问时会自动生成）"
                        
                        response = f"WebUI 状态：已启用\n"
                        response += f"访问地址：{access_url}\n"
                        response += f"认证状态：{auth_status}\n"
                        response += f"访问密码：{password}\n"
                        response += f"会话超时：{self.webui_session_timeout} 秒"
                    else:
                        response = f"WebUI 已启用但启动失败，请检查配置和端口占用情况。\n"
                        response += f"配置的端口：{self.webui_port}\n"
                        response += f"配置的地址：{self.webui_host}"
                except Exception as e:
                    response = f"WebUI 已启用但启动失败：{str(e)}"
        else:
            response = "WebUI 未启用，请在配置中开启。"
        
        yield event.plain_result(response)

    async def _render_status_report(self, event: AstrMessageEvent):
        """输出当前运行状态和关键诊断信息。"""
        report = await self._build_kpi_doctor_report(event)
        yield event.plain_result(report)

    async def _render_preset_list(self, event: AstrMessageEvent):
        """列出所有自定义预设 /kpi p"""
        if not self.parsed_custom_presets:
            yield event.plain_result(
                "当前还没有自定义预设。\n"
                "用法: /kpi y [预设序号] [间隔秒数] [触发概率]\n"
                "例如: /kpi y 1 90 30"
            )
            return
        
        msg = "当前自定义预设：\n"
        for i, preset in enumerate(self.parsed_custom_presets):
            current_marker = ""
            if i == self.current_preset_index:
                current_marker = " <- 当前使用"
            msg += f"{i}. {preset['name']}: {preset['check_interval']} 秒间隔，{preset['trigger_probability']}% 触发概率{current_marker}\n"
        
        msg += f"\n当前使用: {'预设 ' + str(self.current_preset_index) if self.current_preset_index >= 0 else '手动配置'}"
        msg += "\n切换预设: /kpi [预设序号]，例如 /kpi 0"
        yield event.plain_result(msg)

    async def _handle_diary_command(self, event: AstrMessageEvent, date: str = None):
        """处理日记查看命令。"""
        import datetime
        import os

        if not self.enable_diary:
            yield event.plain_result("补写日记失败了，这次没有成功保存。")
            return

        # 确定要查看的日期
        if date:
            try:
                # 支持两种日期格式：YYYY-MM-DD 和 YYYYMMDD
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
            target_date = self._resolve_diary_target_date(now)
            if now.hour < 2:
                yield event.plain_result(
                    f"当前时间还在凌晨两点前，默认查看 {target_date.strftime('%Y年%m月%d日')} 的日记。"
                )

        # 构建日记文件路径
        diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
        diary_path = os.path.join(self.diary_storage, diary_filename)

        if not os.path.exists(diary_path):
            yield event.plain_result(
                f"{target_date.strftime('%Y年%m月%d日')} 的日记还不存在。"
            )
            return

        try:
            with open(diary_path, encoding="utf-8") as f:
                diary_content = f.read()
            
            # 更新日记查看状态
            date_str = target_date.strftime("%Y%m%d")
            self._update_diary_view_status(date_str)

            # 提取感想部分
            summary_start = diary_content.find("## 今日感想")
            if summary_start != -1:
                summary_content = diary_content[summary_start:]
                # 提取感想文本并去除标题
                summary_lines = summary_content.split('\n')
                # 跳过标题行和空行
                start_idx = 2
                while start_idx < len(summary_lines) and (summary_lines[start_idx].strip().startswith('#') or not summary_lines[start_idx].strip()):
                    start_idx += 1
                summary_text = '\n'.join(summary_lines[start_idx:]).strip()
                summary_text = self._extract_diary_preview_text(summary_text)
                if len(summary_text) > 500:
                    summary_text = summary_text[:497] + "..."
                diary_message = f"{self.bot_name} 的日记\n{target_date.strftime('%Y年%m月%d日')}\n\n{summary_text or '这篇日记里还没有整理出完整感想。'}"
            else:
                # 尝试提取旧格式的总结部分
                summary_start = diary_content.find(f"## {self.bot_name}的总结")
                if summary_start == -1:
                    summary_start = diary_content.find("## 总结")
                if summary_start != -1:
                    summary_content = diary_content[summary_start:]
                    # 提取总结文本并去除标题
                    summary_lines = summary_content.split('\n')
                    summary_text = self._extract_diary_preview_text('\n'.join(summary_lines[2:]).strip())
                    if len(summary_text) > 500:
                        summary_text = summary_text[:497] + "..."
                    diary_message = f"{self.bot_name} 的日记\n{target_date.strftime('%Y年%m月%d日')}\n\n{summary_text or '这篇日记里还没有整理出完整感想。'}"
                else:
                    observation_start = diary_content.find("## 今日观察")
                    if observation_start != -1:
                        observation_content = diary_content[observation_start:]
                        observation_lines = observation_content.split('\n')
                        observation_text = self._extract_diary_preview_text('\n'.join(observation_lines[2:]).strip())
                        if len(observation_text) > 500:
                            observation_text = observation_text[:497] + "..."
                        diary_message = f"{self.bot_name} 的日记\n{target_date.strftime('%Y年%m月%d日')}\n\n{observation_text or '这篇日记里还没有可展示的内容。'}"
                    else:
                        diary_message = self._extract_diary_preview_text(diary_content) or "这篇日记里还没有可展示的内容。"

            if self.diary_auto_recall:
                logger.info(f"日记消息将在 {self.diary_recall_time} 秒后自动撤回")

                # 启动自动撤回任务
                async def recall_message():
                    await asyncio.sleep(self.diary_recall_time)
                    try:
                        logger.info(f"日记消息已到达自动撤回时间: {self.diary_recall_time} 秒")
                    except Exception as e:
                        logger.error(f"自动撤回日记记录失败: {e}")

                task = asyncio.create_task(recall_message())
                self.background_tasks.append(task)

            send_as_image = self.diary_send_as_image
            
            if send_as_image:
                try:
                    temp_file_path = self._generate_diary_image(diary_message)
                    yield event.image_result(temp_file_path)
                    os.unlink(temp_file_path)
                except Exception as e:
                    logger.error(f"生成日记图片失败: {e}")
                    yield event.plain_result(diary_message)
            else:
                yield event.plain_result(diary_message)

            # 同时生成日记被查看时的补充回复（异步进行）
            async def generate_blame():
                provider = self.context.get_using_provider()
                if provider:
                    try:
                        system_prompt = await self._get_persona_prompt(event.unified_msg_origin)
                        response = await provider.text_chat(
                            prompt=self.diary_response_prompt, system_prompt=system_prompt
                        )
                        if (
                            response
                            and hasattr(response, "completion_text")
                            and response.completion_text
                        ):
                            await self.context.send_message(
                                event.unified_msg_origin, 
                                MessageChain([Plain(response.completion_text)])
                            )
                        else:
                            await self.context.send_message(
                                event.unified_msg_origin, 
                                MessageChain([Plain("喂，你怎么又偷看我的日记呀，真是的……")])
                            )
                    except Exception as e:
                        logger.error(f"生成日记被偷看回复失败: {e}")
                        await self.context.send_message(
                            event.unified_msg_origin, 
                            MessageChain([Plain("喂，你怎么又偷看我的日记呀，真是的……")])
                        )
                else:
                    await self.context.send_message(
                        event.unified_msg_origin, 
                        MessageChain([Plain("喂，你怎么又偷看我的日记呀，真是的……")])
                    )

            # 异步生成这条补充回复
            blame_task = asyncio.create_task(generate_blame())
            self.background_tasks.append(blame_task)

        except Exception as e:
            logger.error(f"读取日记失败: {e}")
            yield event.plain_result("读取这篇日记时出了点问题。")

    async def _handle_complete_command(self, event: AstrMessageEvent, date: str = None):
        """处理补写日记命令。"""
        import datetime
        import os

        if not self.enable_diary:
            yield event.plain_result("当前没有开启日记功能，暂时无法补写。")
            return

        # 确定要补写的日期
        if date:
            try:
                # 支持两种日期格式：YYYY-MM-DD 和 YYYYMMDD
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
            target_date = self._resolve_diary_target_date(now)
            if now.hour < 2:
                yield event.plain_result(
                    f"当前时间还在凌晨两点前，默认补写 {target_date.strftime('%Y年%m月%d日')} 的日记。"
                )

        # 检查这一天的日记是否已经存在
        diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
        diary_path = os.path.join(self.diary_storage, diary_filename)

        if os.path.exists(diary_path):
            yield event.plain_result(
                f"{target_date.strftime('%Y年%m月%d日')} 的日记已经存在，无需补写。"
            )
            return

        # 生成补写日记
        provider = self.context.get_using_provider()
        if not provider:
            yield event.plain_result("当前没有可用的模型提供商，暂时无法补写日记。")
            return

        try:
            # 获取人格设定
            umo = None
            if event and hasattr(event, "unified_msg_origin"):
                umo = event.unified_msg_origin
            system_prompt = await self._get_persona_prompt(umo)
            # 兜底默认值，避免分支调整时出现未定义变量
            weather_info = ""
            observation_text = ""
            
            # 筛选当天的观察记录
            target_date_str = target_date.strftime("%Y-%m-%d")
            day_observations = []
            for obs in self.observations:
                if obs.get("timestamp", "").startswith(target_date_str):
                    day_observations.append(obs)
            
            # 准备观察记录文本
            if day_observations:
                observation_text = "当天观察记录：\n"
                for i, obs in enumerate(day_observations, 1):
                    observation_text += f"{i}. 场景：{obs.get('scene', '未知')} - {obs.get('description', '')}\n"
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
                completion_prompt += f"\n当天观察记录：\n"
                for obs in day_observations:
                    completion_prompt += f"- {obs.get('scene', '未知')}：{obs.get('description', '')}\n"


            reference_days = []
            for i in range(1, 3):  # 参考前两天的日记语气
                past_date = target_date - datetime.timedelta(days=i)
                past_diary_filename = f"diary_{past_date.strftime('%Y%m%d')}.md"
                past_diary_path = os.path.join(
                    self.diary_storage, past_diary_filename
                )
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

            # 生成日记内容
            response = await provider.text_chat(
                prompt=completion_prompt, system_prompt=system_prompt
            )

            if (
                response
                and hasattr(response, "completion_text")
                and response.completion_text
            ):
                # 获取星期
                weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
                weekday = weekdays[target_date.weekday()]

                # 尝试获取天气信息
                try:
                    weather_info = await self._get_weather_prompt(target_date)
                except Exception as e:
                    logger.debug(f"获取天气信息失败: {e}")

                diary_content = self._build_diary_document(
                    target_date=target_date,
                    weekday=weekday,
                    weather_info=weather_info,
                    observation_text=observation_text,
                    reflection_text=self._ensure_diary_reflection_text(
                        response.completion_text,
                        observation_text,
                    ),
                )

                # 保存日记文件
                try:
                    with open(diary_path, "w", encoding="utf-8") as f:
                        f.write(diary_content)
                    logger.info(f"补写日记已保存到: {diary_path}")
                    yield event.plain_result(f"已补写并保存 {target_date.strftime('%Y年%m月%d日')} 的日记。")
                except Exception as e:
                    logger.error(f"保存补写日记失败: {e}")
                    yield event.plain_result("补写成功了，但保存日记时出了点问题。")
            else:
                yield event.plain_result("模型没有返回有效内容，这次补写没有成功。")
        except Exception as e:
            logger.error(f"补写日记失败: {e}")
            yield event.plain_result("补写日记时出了点问题，请稍后再试。")

    def _is_in_active_time_range(self):
        """检查当前时间是否在活跃时间段内。"""
        # 使用配置中的活跃时间段
        time_range = self.active_time_range

        if not time_range:
            return True

        try:
            import datetime

            now = datetime.datetime.now().time()
            start_str, end_str = time_range.split("-")
            start_hour, start_minute = map(int, start_str.split(":"))
            end_hour, end_minute = map(int, end_str.split(":"))

            start_time = datetime.time(start_hour, start_minute)
            end_time = datetime.time(end_hour, end_minute)

            if start_time <= end_time:
                return start_time <= now <= end_time
            else:
                # 跨午夜的情况
                return now >= start_time or now <= end_time
        except Exception as e:
            logger.error(f"解析时间段失败: {e}")
            return True

    def _is_in_rest_time_range(self):
        """检查当前时间是否在休息时间段内。"""
        configured_range = self._get_configured_rest_range()
        if configured_range is None:
            return False

        try:
            now = datetime.datetime.now().time()
            start_minutes, end_minutes = configured_range
            inferred = self._infer_rest_behavior()
            effective_start_minutes = start_minutes
            inferred_rest_minutes = inferred.get("rest_extended_minutes")
            if inferred.get("available") and inferred_rest_minutes is not None:
                effective_start_minutes = int(inferred_rest_minutes) % (24 * 60)

            start_time = datetime.time(
                effective_start_minutes // 60,
                effective_start_minutes % 60,
            )
            end_time = datetime.time(end_minutes // 60, end_minutes % 60)

            if start_time <= end_time:
                return start_time <= now <= end_time
            else:
                # 跨午夜的情况
                return now >= start_time or now <= end_time
        except Exception as e:
            logger.error(f"解析休息时间段失败: {e}")
            return False

    def _is_in_rest_reminder_range(self):
        """检查当前是否应触发一次休息提醒。"""
        try:
            should_send, _ = self._should_send_rest_reminder()
            return should_send
        except Exception as e:
            logger.error(f"解析休息提醒时间段失败: {e}")
            return False

    def _add_diary_entry(self, content: str, active_window: str):
        """添加日记条目。"""
        if not self.enable_diary:
            return False

        import datetime
        should_store, reason = self._should_store_diary_entry(content, active_window)
        if not should_store:
            logger.info(f"跳过写入日记条目: {reason}")
            return False

        now = datetime.datetime.now()
        entry = {
            "time": now.strftime("%H:%M:%S"),
            "content": content,
            "active_window": active_window,
        }
        self.diary_entries.append(entry)
        # 保留最多10条当天记录，避免日记生成时间在凌晨2点前时丢失数据
        if len(self.diary_entries) > 10:
            self.diary_entries = self.diary_entries[-10:]
        logger.info(f"添加日记条目: {entry}")
        return True

    async def _generate_diary(self, target_date: datetime.date | None = None):
        """生成日记。"""
        if not self.enable_diary or not self.diary_entries:
            return

        import datetime

        target_date = target_date or datetime.date.today()
        # 获取星期
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekdays[target_date.weekday()]

        # 尝试获取天气信息
        weather_info = ""
        try:
            weather_info = await self._get_weather_prompt()
        except Exception as e:
            logger.debug(f"获取天气信息失败: {e}")

        # 构建标准格式的日记内容
        compacted_entries = self._compact_diary_entries(self.diary_entries)
        if not compacted_entries:
            logger.info("今日日记没有可用的高质量观察条目，已跳过生成")
            # 生成一篇"没观察"的日记，bot 抱怨用户没给机会
            provider = self.context.get_using_provider()
            if provider:
                try:
                    system_prompt = await self._get_persona_prompt()
                    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
                    weekday = weekdays[target_date.weekday()]
                    weather_info = ""
                    try:
                        weather_info = await self._get_weather_prompt()
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
                        reflection_text = self._ensure_diary_reflection_text(
                            response.completion_text,
                            "（今天用户没给我看屏幕的机会，呜呜）",
                        )
                        diary_content = self._build_diary_document(
                            target_date=target_date,
                            weekday=weekday,
                            weather_info=weather_info,
                            observation_text="（今天用户没给我看屏幕的机会，呜呜）",
                            reflection_text=reflection_text,
                        )
                        diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
                        diary_path = os.path.join(self.diary_storage, diary_filename)
                        with open(diary_path, "w", encoding="utf-8") as f:
                            f.write(diary_content)
                        logger.info(f"日记已保存（无观察）: {diary_path}")
                except Exception as e:
                    logger.error(f"生成无观察日记失败: {e}")
            self.diary_entries = []
            self.last_diary_date = target_date
            return
        observation_lines = []
        for entry in compacted_entries:
            time_label = entry["start_time"] if entry["start_time"] == entry["end_time"] else f"{entry['start_time']}-{entry['end_time']}"
            observation_lines.append(f"### {time_label} - {entry['active_window']}")
            if len(entry["points"]) == 1:
                observation_lines.append(entry["points"][0])
            else:
                for point in entry["points"]:
                    observation_lines.append(f"- {point}")
            observation_lines.append("")
        observation_text = "\n".join(observation_lines).strip()
        reflection_text = ""

        import datetime
        viewed_count = 0
        for i in range(1, 4):
            past_date = target_date - datetime.timedelta(days=i)
            past_date_str = past_date.strftime("%Y%m%d")
            if past_date_str in self.diary_metadata and self.diary_metadata[past_date_str].get("viewed", False):
                viewed_count += 1
        
        logger.info(f"最近三天日记查看次数: {viewed_count}")

        # 生成带风格的今日日记总结
        provider = self.context.get_using_provider()
        if provider:
            if len(compacted_entries) < 2:
                summary_prompt = (
                    "今天的观察还比较少，请写一段简短、自然、不过度脑补的今日日记。"
                    "可以更克制一点，但仍然要保留一点真实感和陪伴感。"
                    "字数控制在 180 到 320 字。"
                )
            else:
                # 根据最近查看次数调整提示词
                reference_days = []
                if self.diary_reference_days > 0:
                    for i in range(1, self.diary_reference_days + 1):
                        past_date = target_date - datetime.timedelta(days=i)
                        past_diary_filename = f"diary_{past_date.strftime('%Y%m%d')}.md"
                        past_diary_path = os.path.join(
                            self.diary_storage, past_diary_filename
                        )
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

                summary_prompt = self._build_diary_reflection_prompt(
                    observation_text=observation_text,
                    viewed_count=viewed_count,
                    reference_days=reference_days,
                )

            try:
                system_prompt = await self._get_persona_prompt()
                response = await provider.text_chat(
                    prompt=summary_prompt, system_prompt=system_prompt
                )
                if (
                    response
                    and hasattr(response, "completion_text")
                    and response.completion_text
                ):
                    reflection_text = self._ensure_diary_reflection_text(
                        response.completion_text,
                        observation_text,
                    )
            except Exception as e:
                logger.error(f"生成日记总结失败: {e}")

        structured_summary = self._build_diary_structured_summary(
            compacted_entries,
            reflection_text,
        )
        reflection_text = self._ensure_diary_reflection_text(
            reflection_text,
            observation_text,
            structured_summary,
        )
        if not structured_summary.get("suggestion_items"):
            structured_summary["suggestion_items"] = self._extract_actionable_suggestions(
                reflection_text,
                limit=3,
            )

        diary_content = self._build_diary_document(
            target_date=target_date,
            weekday=weekday,
            weather_info=weather_info,
            observation_text=observation_text,
            reflection_text=reflection_text,
            structured_summary=structured_summary,
        )

        # 保存日记文件
        diary_filename = f"diary_{target_date.strftime('%Y%m%d')}.md"
        diary_path = os.path.join(self.diary_storage, diary_filename)

        try:
            with open(diary_path, "w", encoding="utf-8") as f:
                f.write(diary_content)
            self._save_diary_structured_summary(target_date, structured_summary)
            self._remember_diary_summary_memories(target_date, structured_summary)
            self._update_memory_priorities()
            self._save_long_term_memory()
            logger.info(f"日记已保存到: {diary_path}")

            # 重置日记条目
            self.diary_entries = []
            self.last_diary_date = target_date

            logger.info("日记生成完成，不自动发送，等待用户主动查看")
        except Exception as e:
            logger.error(f"保存日记失败: {e}")
