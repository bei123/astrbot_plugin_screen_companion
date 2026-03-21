"""TCP screen relay: receive JPEG frames from the Windows push client.

The host object (ScreenCompanion) must expose the attributes written by this module;
see run_screen_relay_server / capture_screen_bytes_remote docstrings.
"""

from __future__ import annotations

import asyncio
import socket as sock_module
import struct
from typing import Any

from astrbot.api import logger

_MAX_IMAGE_BYTES = 20 * 1024 * 1024


async def run_screen_relay_server(host: Any) -> None:
    """Listen on ``host.screen_relay_bind`` / ``host.screen_relay_port``.

    Expects: ``host.running``, ``host._latest_remote_*``, ``host._remote_image_event``,
    ``host._remote_relay_server`` (set/cleared by this coroutine).
    """
    async def _handle_client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while host.running:
                try:
                    first = await reader.readexactly(1)
                    if first[0] == 0xFE:
                        mic_b = await reader.readexactly(1)
                        host._latest_remote_mic_level = min(100, max(0, mic_b[0]))
                        continue
                    if first[0] == 0xFF:
                        _version = (await reader.readexactly(1))[0]
                        title_len_b = await reader.readexactly(3)
                        title_len = struct.unpack(">I", b"\x00" + title_len_b)[0]
                    else:
                        _version = 0
                        title_len_b = await reader.readexactly(3)
                        title_len = struct.unpack(">I", first + title_len_b)[0]
                    title_bytes = await reader.readexactly(title_len) if title_len else b""
                    active_window_title = (
                        title_bytes.decode("utf-8", errors="replace") if title_bytes else ""
                    )
                    img_len_b = await reader.readexactly(4)
                    img_len = struct.unpack(">I", img_len_b)[0]
                    if img_len <= 0 or img_len > _MAX_IMAGE_BYTES:
                        break
                    image_bytes = await reader.readexactly(img_len)
                    mic_level = 0
                    if _version >= 1:
                        try:
                            mic_b = await reader.readexactly(1)
                            mic_level = min(100, max(0, mic_b[0]))
                        except asyncio.IncompleteReadError:
                            pass
                    host._latest_remote_image_bytes = image_bytes
                    host._latest_remote_window_title = active_window_title
                    host._latest_remote_mic_level = mic_level
                    host._remote_image_event.set()
                    logger.debug(
                        "收到远程截图: %s bytes, 窗口: %s, 麦克风: %s",
                        len(image_bytes),
                        active_window_title[:50],
                        mic_level,
                    )
                except asyncio.IncompleteReadError:
                    break
                except asyncio.CancelledError:
                    break
        except Exception as e:
            logger.debug("远程截图连接处理结束: %s", e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    try:
        sock = sock_module.socket(sock_module.AF_INET, sock_module.SOCK_STREAM)
        sock.setsockopt(sock_module.SOL_SOCKET, sock_module.SO_REUSEADDR, 1)
        sock.bind((host.screen_relay_bind, host.screen_relay_port))
        sock.listen(128)
        sock.setblocking(False)
        host._remote_relay_server = await asyncio.start_server(
            _handle_client,
            sock=sock,
        )
        addr = host._remote_relay_server.sockets[0].getsockname()
        logger.info(
            "屏幕伴侣远程截图服务已启动，监听 %s:%s，等待 Windows 端连接并推送截图。",
            addr[0],
            addr[1],
        )
        async with host._remote_relay_server:
            await host._remote_relay_server.serve_forever()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("远程截图服务异常: %s", e)
    finally:
        if host._remote_relay_server:
            host._remote_relay_server.close()
            await host._remote_relay_server.wait_closed()
            host._remote_relay_server = None
        logger.info("屏幕伴侣远程截图服务已停止")


async def capture_screen_bytes_remote(
    host: Any, *, wait_timeout: float = 15.0
) -> tuple[bytes, str]:
    """Return latest pushed JPEG and window title; wait up to ``wait_timeout`` if empty."""
    if host._latest_remote_image_bytes is not None:
        return (bytes(host._latest_remote_image_bytes), host._latest_remote_window_title)
    try:
        await asyncio.wait_for(host._remote_image_event.wait(), timeout=wait_timeout)
    except asyncio.TimeoutError:
        return (b"", "未收到远程截图")
    if host._latest_remote_image_bytes is None:
        return (b"", "未收到远程截图")
    return (bytes(host._latest_remote_image_bytes), host._latest_remote_window_title)
