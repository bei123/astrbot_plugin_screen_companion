"""WebUI 生命周期：端口规范化、密码保障、启停与重启（HTTP 路由留在 web_server）。"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from astrbot.api import logger

DEFAULT_WEBUI_PORT = 6314

WebuiRuntimeSnapshot = tuple[bool, str, int, str, int]


def normalize_webui_port(host: Any, port: Any) -> int:
    """将端口限制在合法范围；无效时回退到 host.DEFAULT_WEBUI_PORT 或模块默认。"""
    default_port = int(getattr(host, "DEFAULT_WEBUI_PORT", DEFAULT_WEBUI_PORT))
    try:
        normalized = int(port)
    except Exception:
        normalized = default_port

    if normalized < 1 or normalized > 65535:
        logger.warning(
            f"WebUI 端口 {port} 不在有效范围内，已自动回退到 {default_port}"
        )
        return default_port
    if normalized < 1024:
        logger.warning(
            f"WebUI 端口 {port} 是系统保留端口，可能需要管理员权限"
        )
    return normalized


def ensure_webui_password(host: Any) -> bool:
    """在启用 WebUI 且开启认证、密码为空时生成随机密码并落盘。"""
    current_password = str(host.plugin_config.webui.password or "").strip()
    if (
        host.plugin_config.webui.enabled
        and host.plugin_config.webui.auth_enabled
        and not current_password
    ):
        generated = f"{secrets.randbelow(1000000):06d}"
        host.plugin_config.webui.password = generated
        host.plugin_config.save_webui_config()
        logger.info(f"WebUI 访问密码已自动生成: {generated}")
        logger.info("请在配置中查看或修改此密码")
        return True
    return False


def snapshot_webui_runtime(host: Any) -> WebuiRuntimeSnapshot:
    return (
        getattr(host, "webui_enabled", False),
        getattr(host, "webui_host", "0.0.0.0"),
        getattr(host, "webui_port", 8898),
        getattr(host, "webui_password", ""),
        getattr(host, "webui_session_timeout", 3600),
    )


def is_webui_runtime_changed(
    host: Any, old_state: WebuiRuntimeSnapshot
) -> bool:
    return old_state != snapshot_webui_runtime(host)


def webui_auth_enabled(plugin: Any) -> bool:
    try:
        return bool(plugin.plugin_config.webui.auth_enabled)
    except Exception:
        return True


def webui_expected_secret(plugin: Any) -> str:
    password = ""
    try:
        password = str(plugin.plugin_config.webui.password or "").strip()
    except Exception:
        password = ""
    if not password:
        return ""
    if not webui_auth_enabled(plugin):
        return ""
    return password


def webui_session_timeout_seconds(plugin: Any) -> int:
    timeout = 3600
    try:
        timeout = int(plugin.plugin_config.webui.session_timeout or 3600)
    except Exception:
        timeout = 3600
    if timeout <= 0:
        timeout = 3600
    return timeout


async def start_webui(host: Any) -> None:
    """在锁内启动 WebServer；失败时清空 host.web_server。"""
    from ..web_server import WebServer

    host._ensure_runtime_state()
    webui_lock = getattr(host, "_webui_lock", None)
    if webui_lock is None:
        host._webui_lock = asyncio.Lock()
        webui_lock = host._webui_lock

    async with webui_lock:
        try:
            if host.web_server:
                logger.info("检测到 Web UI 服务器已存在，正在停止旧实例...")
                await host.web_server.stop()
                host.web_server = None
                await asyncio.sleep(1.0)

            host.web_server = WebServer(host, host=host.webui_host, port=host.webui_port)
            success = await host.web_server.start()
            if not success:
                host.web_server = None
                logger.error(
                    f"WebUI 启动失败，原因: 无法绑定 {host.webui_host}:{host.webui_port}"
                )
        except Exception as e:
            host.web_server = None
            logger.error(f"启动 Web UI 时出错: {e}")


async def stop_webui(host: Any) -> None:
    host._ensure_runtime_state()
    webui_lock = getattr(host, "_webui_lock", None)
    if webui_lock is None:
        host._webui_lock = asyncio.Lock()
        webui_lock = host._webui_lock

    async with webui_lock:
        if host.web_server:
            try:
                await host.web_server.stop()
            except Exception as e:
                logger.error(f"停止 Web UI 时出错: {e}")
            finally:
                host.web_server = None


async def restart_webui(host: Any) -> None:
    """配置变更后重启：禁用时仅停止；启用时先起新实例再停旧实例。"""
    from ..web_server import WebServer

    host._ensure_runtime_state()
    webui_lock = getattr(host, "_webui_lock", None)
    if webui_lock is None:
        host._webui_lock = asyncio.Lock()
        webui_lock = host._webui_lock

    async with webui_lock:
        logger.info("检测到 WebUI 配置变更，正在重启 WebUI...")

        if not host.webui_enabled:
            if host.web_server:
                await host.web_server.stop()
                host.web_server = None
                await asyncio.sleep(0.6)
            return

        old_server = host.web_server

        try:
            new_server = WebServer(host, host=host.webui_host, port=host.webui_port)
            success = await new_server.start()
            if success:
                host.web_server = new_server
                if old_server:
                    try:
                        await old_server.stop()
                        await asyncio.sleep(0.6)
                    except Exception as e:
                        logger.warning(f"停止旧 WebUI 服务时出错: {e}")
                logger.info("WebUI 重启成功")
            else:
                host.web_server = None
                logger.error(
                    f"WebUI 重启失败，原因: 无法绑定 {host.webui_host}:{host.webui_port}"
                )
                if old_server and host.web_server != old_server:
                    host.web_server = old_server
                    logger.info("已恢复旧的 WebUI 服务")
        except Exception as e:
            host.web_server = None
            logger.error(f"重启 WebUI 失败: {e}")
            if old_server and host.web_server != old_server:
                host.web_server = old_server
                logger.info("已恢复旧的 WebUI 服务")
