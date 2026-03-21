# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import secrets
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import BaseMessageComponent, Plain

from ..web_server import WebServer


class ScreenCompanionProactiveMixin:
    def _parse_custom_presets(self) -> list:
        """解析自定义预设配置。"""
        self.parsed_custom_presets = []
        if not self.custom_presets:
            return self.parsed_custom_presets

        lines = self.custom_presets.strip().split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split('|')
            if len(parts) >= 3:
                try:
                    preset = {
                        "name": parts[0].strip(),
                        "check_interval": max(10, int(parts[1].strip())),
                        "trigger_probability": max(0, min(100, int(parts[2].strip())))
                    }
                    self.parsed_custom_presets.append(preset)
                except ValueError:
                    continue
        return self.parsed_custom_presets

    def _get_current_preset_params(self) -> tuple:
        """获取当前生效的预设参数。"""
        if self.current_preset_index >= 0 and self.current_preset_index < len(self.parsed_custom_presets):
            preset = self.parsed_custom_presets[self.current_preset_index]
            return preset["check_interval"], preset["trigger_probability"]
        return self.check_interval, self.trigger_probability

    def _parse_window_companion_targets(self):
        """Parse window companion rules from config text."""
        self.parsed_window_companion_targets = []
        raw_text = str(getattr(self, "window_companion_targets", "") or "").strip()
        if not raw_text:
            return self.parsed_window_companion_targets

        for line in raw_text.splitlines():
            entry = line.strip()
            if not entry:
                continue

            keyword, prompt = entry, ""
            if "|" in entry:
                keyword, prompt = entry.split("|", 1)

            keyword = keyword.strip()
            prompt = prompt.strip()
            if not keyword:
                continue

            self.parsed_window_companion_targets.append(
                {
                    "keyword": keyword,
                    "keyword_lower": keyword.casefold(),
                    "prompt": prompt,
                }
            )

        return self.parsed_window_companion_targets

    def _list_open_window_titles(self) -> list[str]:
        """Return de-duplicated open window titles."""
        try:
            import pygetwindow
        except ImportError:
            return []
        except Exception as e:
            logger.debug(f"读取窗口列表失败: {e}")
            return []

        raw_titles = []
        try:
            raw_titles = list(pygetwindow.getAllTitles())
        except Exception:
            try:
                raw_titles = [getattr(window, "title", "") for window in pygetwindow.getAllWindows()]
            except Exception as e:
                logger.debug(f"读取窗口标题失败: {e}")
                return []

        titles = []
        seen = set()
        for title in raw_titles:
            normalized = self._normalize_window_title(title)
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            titles.append(normalized)
        return titles

    def _match_window_companion_target(self, window_titles):
        """Find the first configured window companion rule that matches."""
        if not window_titles or not getattr(self, "parsed_window_companion_targets", None):
            return None, ""

        for rule in self.parsed_window_companion_targets:
            keyword = rule.get("keyword_lower", "")
            if not keyword:
                continue
            for title in window_titles:
                if keyword in str(title or "").casefold():
                    return rule, title
        return None, ""

    def _get_default_target(self) -> str:
        """Resolve the proactive message target."""
        target = str(getattr(self, "proactive_target", "") or "").strip()
        if target:
            return self._normalize_target(target)

        admin_qq = str(getattr(self, "admin_qq", "") or "").strip()
        if admin_qq:
            return self._build_private_target(admin_qq)
        return ""

    def _get_available_platforms(self) -> list[Any]:
        """Return loaded platform instances, preferring non-webchat adapters."""
        platform_manager = getattr(self.context, "platform_manager", None)
        if not platform_manager:
            return []

        platforms = list(getattr(platform_manager, "platform_insts", []) or [])
        if not platforms:
            return []

        filtered = []
        for platform in platforms:
            try:
                meta = platform.meta()
                if str(getattr(meta, "name", "") or "").strip() == "webchat":
                    continue
            except Exception:
                pass
            filtered.append(platform)
        return filtered or platforms

    def _get_preferred_platform_id(self) -> str:
        """Resolve the platform instance ID used for proactive messages."""
        platforms = self._get_available_platforms()
        if platforms:
            try:
                platform_id = str(getattr(platforms[0].meta(), "id", "") or "").strip()
                if platform_id:
                    return platform_id
            except Exception as e:
                logger.debug(f"获取默认平台 ID 失败: {e}")
        return "default"

    def _build_private_target(self, session_id: str) -> str:
        """Build a private-chat target with the active platform instance ID."""
        session_id = str(session_id or "").strip()
        if not session_id:
            return ""
        return f"{self._get_preferred_platform_id()}:FriendMessage:{session_id}"

    def _normalize_target(self, target: str) -> str:
        """Rewrite legacy proactive targets to the active platform instance ID."""
        target = str(target or "").strip()
        if not target:
            return ""

        parts = target.split(":", 2)
        if len(parts) != 3:
            return target

        platform_token, message_type, session_id = parts
        platforms = self._get_available_platforms()
        if not platforms:
            return target

        for platform in platforms:
            try:
                meta = platform.meta()
                platform_id = str(getattr(meta, "id", "") or "").strip()
                platform_name = str(getattr(meta, "name", "") or "").strip()
            except Exception:
                continue

            if platform_token in {platform_id, platform_name}:
                normalized = f"{platform_id}:{message_type}:{session_id}"
                if normalized != target:
                    logger.info(f"主动消息目标已规范化: {target} -> {normalized}")
                return normalized

        legacy_platform_tokens = {
            "default",
            "aiocqhttp",
            "qq_official",
            "qq_official_webhook",
            "telegram",
            "discord",
            "wecom",
            "wecom_ai_bot",
            "weixin_official_account",
            "line",
            "kook",
            "satori",
            "lark",
            "dingtalk",
            "misskey",
            "slack",
        }
        if len(platforms) == 1 and platform_token in legacy_platform_tokens:
            try:
                platform_id = str(getattr(platforms[0].meta(), "id", "") or "").strip()
            except Exception:
                platform_id = ""
            if platform_id:
                normalized = f"{platform_id}:{message_type}:{session_id}"
                if normalized != target:
                    logger.info(f"主动消息目标已回退到当前平台实例 ID: {target} -> {normalized}")
                return normalized

        return target

    def _create_virtual_event(self, target: str):
        """Build a lightweight virtual event for proactive tasks."""
        event = type("VirtualEvent", (), {})()
        event.unified_msg_origin = self._normalize_target(target)
        event.config = self.plugin_config
        return event

    async def _send_proactive_message(
        self, target: str, message_chain: MessageChain
    ) -> bool:
        """Send a proactive message via the resolved platform instance."""
        target = self._normalize_target(target)
        if not target:
            return False

        session = None
        try:
            from astrbot.core.platform.message_session import MessageSesion

            session = MessageSesion.from_str(target)
        except Exception as e:
            logger.debug(f"解析主动消息目标失败，将回退到 context.send_message: {e}")

        if session is not None:
            platforms = self._get_available_platforms()
            matched_platform = None
            for platform in platforms:
                try:
                    meta = platform.meta()
                    platform_id = str(getattr(meta, "id", "") or "").strip()
                    platform_name = str(getattr(meta, "name", "") or "").strip()
                except Exception:
                    continue
                if session.platform_name in {platform_id, platform_name}:
                    matched_platform = platform
                    if session.platform_name != platform_id:
                        session = MessageSesion(
                            platform_id, session.message_type, session.session_id
                        )
                    break

            if matched_platform is None and platforms:
                matched_platform = platforms[0]
                try:
                    fallback_platform_id = str(
                        getattr(matched_platform.meta(), "id", "") or ""
                    ).strip()
                    if fallback_platform_id:
                        session = MessageSesion(
                            fallback_platform_id,
                            session.message_type,
                            session.session_id,
                        )
                        logger.info(
                            f"主动消息目标未命中平台，已回退为 {fallback_platform_id}:{session.message_type.value}:{session.session_id}"
                        )
                except Exception as e:
                    logger.debug(f"构造主动消息回退会话失败: {e}")

            if matched_platform is not None:
                try:
                    await matched_platform.send_by_session(session, message_chain)
                    return True
                except Exception as e:
                    logger.warning(f"主动消息直发失败，将回退到 context.send_message: {e}")

        try:
            await self.context.send_message(target, message_chain)
            return True
        except Exception as e:
            logger.error(f"发送主动消息失败: {e}")
            return False

    async def _send_plain_message(self, target: str, text: str) -> bool:
        """Send a plain proactive message if possible."""
        target = str(target or "").strip()
        text = str(text or "").strip()
        if not target or not text:
            return False

        return await self._send_proactive_message(
            target, MessageChain([Plain(text)])
        )

    def _resolve_proactive_target(self, fallback_event: Any = None) -> str:
        target = self._get_default_target()
        if not target and fallback_event is not None:
            try:
                target = str(getattr(fallback_event, "unified_msg_origin", "") or "").strip()
            except Exception as e:
                logger.debug(f"读取回退主动消息目标失败: {e}")
        return self._normalize_target(target)

    def _build_message_chain(
        self, components: list[BaseMessageComponent] | None
    ) -> MessageChain:
        chain = MessageChain()
        for comp in components or []:
            chain.chain.append(comp)
        return chain

    def _extract_plain_text(
        self, components: list[BaseMessageComponent] | None
    ) -> str:
        chunks: list[str] = []
        for comp in components or []:
            if isinstance(comp, Plain):
                text = str(getattr(comp, "text", "") or "")
                if text:
                    chunks.append(text)
        return "".join(chunks)

    async def _send_component_text(
        self,
        target: str,
        components: list[BaseMessageComponent] | None,
        *,
        prefix: str = "",
    ) -> bool:
        text = self._extract_plain_text(components)
        if not text:
            return False
        if prefix:
            text = f"{prefix}\n{text}"
        return await self._send_plain_message(target, text)

    async def _send_segmented_text(
        self,
        target: str,
        text: str,
        *,
        max_length: int = 1000,
        delay_seconds: float = 0.5,
        should_continue: Any = None,
    ) -> bool:
        target = str(target or "").strip()
        text = str(text or "").strip()
        if not target or not text:
            return False

        segments = self._split_message(text, max_length=max_length)
        if not segments:
            return False

        sent = False
        for index, segment in enumerate(segments):
            if should_continue is not None and not should_continue():
                break
            if not segment.strip():
                continue
            sent = await self._send_plain_message(target, segment) or sent
            if index < len(segments) - 1 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
        return sent

    def _build_window_companion_prompt(self, window_title: str, extra_prompt: str = "") -> str:
        """Build a focused prompt for window companion sessions."""
        pieces = [
            f"这是你被指定要陪伴的窗口：《{window_title}》。",
            "请更关注这个窗口里的当前任务、卡点和下一步，不要泛泛播报画面。",
            "如果适合给建议，优先给和当前任务直接相关、能立刻派上用场的建议。",
            "保持对话的连续性，关注用户的任务进展，提供具体的建议。",
            "注意观察窗口内容的变化，及时调整你的回应，确保与当前场景相关。",
            "如果发现用户遇到困难，提供具体的解决方案和步骤指导。",
        ]
        if extra_prompt:
            pieces.append(extra_prompt.strip())
        return "\n".join(piece for piece in pieces if piece)

    def _is_window_companion_session_active(self) -> bool:
        task = (getattr(self, "auto_tasks", {}) or {}).get(
            getattr(self, "WINDOW_COMPANION_TASK_ID", "")
        )
        return bool(task and not task.done())

    async def _start_window_companion_session(self, window_title: str, rule: dict) -> bool:
        """Start automatic companion mode for a matched window."""
        self._ensure_runtime_state()
        if not self.enabled or not self.enable_window_companion:
            return False
        if self._is_window_companion_session_active():
            return False

        target = self._get_default_target()
        if not target:
            logger.warning("窗口陪伴已匹配到目标窗口，但没有可用的主动消息目标，已跳过启动")
            return False

        ok, err_msg = self._check_env(check_mic=False)
        if not ok:
            logger.warning(f"窗口陪伴启动失败: {err_msg}")
            return False

        event = self._create_virtual_event(target)
        self.window_companion_active_title = window_title
        self.window_companion_active_target = target
        self.window_companion_active_rule = dict(rule or {})
        self.is_running = True
        self.state = "active"
        self.auto_tasks[self.WINDOW_COMPANION_TASK_ID] = asyncio.create_task(
            self._auto_screen_task(
                event,
                task_id=self.WINDOW_COMPANION_TASK_ID,
                custom_prompt=self._build_window_companion_prompt(
                    window_title, (rule or {}).get("prompt", "")
                ),
            )
        )

        start_response = await self._get_start_response(target)
        intro = f"检测到《{window_title}》已经打开，我来陪你。"
        await self._send_plain_message(target, f"{intro}\n{start_response}".strip())
        logger.info(f"窗口陪伴已启动: {window_title}")
        return True

    async def _stop_window_companion_session(self, reason: str = "window_closed") -> bool:
        """Stop the automatic companion session for the matched window."""
        self._ensure_runtime_state()
        task_id = getattr(self, "WINDOW_COMPANION_TASK_ID", "")
        task = (getattr(self, "auto_tasks", {}) or {}).get(task_id)
        if not task and not getattr(self, "window_companion_active_title", ""):
            return False

        active_title = str(getattr(self, "window_companion_active_title", "") or "").strip()
        target = str(getattr(self, "window_companion_active_target", "") or "").strip()

        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("等待窗口陪伴任务停止超时")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"停止窗口陪伴任务失败: {e}")

        self.auto_tasks.pop(task_id, None)
        self.window_companion_active_title = ""
        self.window_companion_active_target = ""
        self.window_companion_active_rule = {}

        if not self.auto_tasks:
            self.is_running = False
            self.state = "inactive"

        if target and active_title:
            end_response = await self._get_end_response(target)
            if reason == "disabled":
                outro = f"《{active_title}》的窗口陪伴已经关闭，我先退到旁边。"
            else:
                outro = f"《{active_title}》已经关掉了，我先退到旁边。"
            await self._send_plain_message(target, f"{outro}\n{end_response}".strip())

        logger.info(f"窗口陪伴已停止: {active_title or 'unknown'} ({reason})")
        return True

    async def _window_companion_task(self):
        """Watch configured windows and start or stop companion sessions automatically."""
        self._ensure_runtime_state()
        while self.running and self._is_current_process_instance():
            interval = max(2, int(getattr(self, "window_companion_check_interval", 5) or 5))
            try:
                if not self.enable_window_companion or not getattr(
                    self, "parsed_window_companion_targets", None
                ):
                    if self._is_window_companion_session_active() or getattr(
                        self, "window_companion_active_title", ""
                    ):
                        await self._stop_window_companion_session(reason="disabled")
                    await asyncio.sleep(interval)
                    continue

                window_titles = self._list_open_window_titles()
                matched_rule, matched_title = self._match_window_companion_target(window_titles)
                active_title = str(getattr(self, "window_companion_active_title", "") or "").strip()
                active_exists = bool(
                    active_title
                    and any(active_title.casefold() == title.casefold() for title in window_titles)
                )

                if matched_rule and matched_title and not self._is_window_companion_session_active():
                    await self._start_window_companion_session(matched_title, matched_rule)
                elif self._is_window_companion_session_active() and not active_exists:
                    await self._stop_window_companion_session(reason="window_closed")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"窗口陪伴监测异常: {e}")

            await asyncio.sleep(interval)

    def _ensure_webui_password(self) -> bool:
        """确保 WebUI 在需要认证时拥有可用密码。"""
        # 检查密码是否已经设置
        current_password = str(self.plugin_config.webui.password or "").strip()
        # 仅当开启认证且密码为空时，自动生成密码
        if (
            self.plugin_config.webui.enabled
            and self.plugin_config.webui.auth_enabled
            and not current_password
        ):
            # 生成随机密码
            generated = f"{secrets.randbelow(1000000):06d}"
            # 保存密码
            self.plugin_config.webui.password = generated
            self.plugin_config.save_webui_config()
            logger.info(f"WebUI 访问密码已自动生成: {generated}")
            logger.info("请在配置中查看或修改此密码")
            return True
        return False

    def _snapshot_webui_runtime(self) -> tuple[bool, str, int, str, int]:
        """返回当前 WebUI 运行时快照。"""
        return (
            getattr(self, "webui_enabled", False),
            getattr(self, "webui_host", "0.0.0.0"),
            getattr(self, "webui_port", 8898),
            getattr(self, "webui_password", ""),
            getattr(self, "webui_session_timeout", 3600),
        )

    def _is_webui_runtime_changed(
        self, old_state: tuple[bool, str, int, str, int]
    ) -> bool:
        return old_state != self._snapshot_webui_runtime()

    async def _restart_webui(self) -> None:
        self._ensure_runtime_state()
        webui_lock = getattr(self, "_webui_lock", None)
        if webui_lock is None:
            self._webui_lock = asyncio.Lock()
            webui_lock = self._webui_lock

        async with webui_lock:
            logger.info("检测到 WebUI 配置变更，正在重启 WebUI...")

            if not self.webui_enabled:
                # WebUI 已禁用，停止旧服务即可
                if self.web_server:
                    await self.web_server.stop()
                    self.web_server = None
                    await asyncio.sleep(0.6)
                return

            # 保存旧服务引用，在新服务启动成功后再停止
            old_server = self.web_server

            try:
                new_server = WebServer(self, host=self.webui_host, port=self.webui_port)
                success = await new_server.start()
                if success:
                    # 新服务启动成功，更新引用并停止旧服务
                    self.web_server = new_server
                    if old_server:
                        try:
                            await old_server.stop()
                            await asyncio.sleep(0.6)
                        except Exception as e:
                            logger.warning(f"停止旧 WebUI 服务时出错: {e}")
                    logger.info("WebUI 重启成功")
                else:
                    self.web_server = None
                    logger.error(
                        f"WebUI 重启失败，原因: 无法绑定 {self.webui_host}:{self.webui_port}"
                    )
                    # 启动失败，恢复旧服务引用
                    if old_server and self.web_server != old_server:
                        self.web_server = old_server
                        logger.info("已恢复旧的 WebUI 服务")
            except Exception as e:
                self.web_server = None
                logger.error(f"重启 WebUI 失败: {e}")
                # 启动失败，恢复旧服务引用
                if old_server and self.web_server != old_server:
                    self.web_server = old_server
                    logger.info("已恢复旧的 WebUI 服务")
