"""Local and shared-directory screen capture (non-remote)."""

from __future__ import annotations

import asyncio
import datetime
import io
import os
import time
from typing import Any

from astrbot.api import logger

# 与原先 main.py 中 resolve_shared_screenshot_dir 一致：插件包目录上两级 + screenshots
_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_shared_screenshots_dir() -> str:
    return os.path.normpath(os.path.join(_PLUGIN_ROOT, "..", "..", "screenshots"))


def capture_screen_bytes_local_sync(host: Any) -> tuple[bytes, str]:
    """同步截取或读取共享目录截图；在事件循环中请通过 ``asyncio.to_thread`` 调用。"""
    from PIL import Image

    shared_dir_enabled = host._get_runtime_flag("use_shared_screenshot_dir")
    configured_shared_dir = str(getattr(host, "shared_screenshot_dir", "") or "").strip()

    def resolve_shared_screenshot_dir() -> str:
        if configured_shared_dir:
            return os.path.normpath(configured_shared_dir)

        env_dir = str(os.environ.get("SCREENSHOT_DIR") or "").strip()
        if env_dir:
            return os.path.normpath(env_dir)

        return _default_shared_screenshots_dir()

    def persist_shared_screenshot(image_bytes: bytes) -> None:
        if not shared_dir_enabled:
            return

        screenshots_dir = resolve_shared_screenshot_dir()
        try:
            os.makedirs(screenshots_dir, exist_ok=True)
            timestamp = int(time.time())
            target_path = os.path.join(screenshots_dir, f"screenshot_{timestamp}.jpg")
            latest_path = os.path.join(screenshots_dir, "screenshot_latest.jpg")
            with open(target_path, "wb") as f:
                f.write(image_bytes)
            with open(latest_path, "wb") as f:
                f.write(image_bytes)
        except Exception as e:
            logger.warning(f"写入共享截图目录失败: {e}")

    def encode_image_to_jpeg_bytes(image):
        if image.mode != "RGB":
            image = image.convert("RGB")
        img_byte_arr = io.BytesIO()
        quality_val = host.image_quality
        try:
            quality = max(10, min(100, int(quality_val)))
        except (ValueError, TypeError):
            quality = 70
        image.save(img_byte_arr, format="JPEG", quality=quality)
        return img_byte_arr.getvalue()

    def capture_live_screenshot():
        import pyautogui

        active_title, active_region = host._get_active_window_info()
        screenshot = None

        if host.capture_active_window and active_region:
            try:
                screenshot = pyautogui.screenshot(region=active_region)
            except Exception as e:
                logger.warning(f"活动窗口截图失败，将回退为全屏截图: {e}")

        if screenshot is None:
            screenshot = pyautogui.screenshot()

        source_label = active_title or (
            "活动窗口截图" if host.capture_active_window else "实时截图"
        )
        image_bytes = encode_image_to_jpeg_bytes(screenshot)
        persist_shared_screenshot(image_bytes)
        return image_bytes, source_label

    if not shared_dir_enabled:
        try:
            return capture_live_screenshot()
        except Exception as e:
            logger.error(f"实时截图失败: {e}")
            raise

    screenshots_dir = resolve_shared_screenshot_dir()

    if not os.path.exists(screenshots_dir):
        logger.warning(f"共享截图目录不存在，将回退为实时截图: {screenshots_dir}")
        try:
            return capture_live_screenshot()
        except Exception as e:
            logger.error(f"实时截图失败: {e}")
            raise

    screenshot_files = [
        f for f in os.listdir(screenshots_dir)
        if f.startswith("screenshot_") and f.endswith(".jpg")
    ]

    if not screenshot_files:
        logger.warning("共享截图目录中没有可用截图，将回退为实时截图")
        try:
            return capture_live_screenshot()
        except Exception as e:
            logger.error(f"实时截图失败: {e}")
            raise

    screenshot_candidates = []
    for filename in screenshot_files:
        screenshot_path = os.path.join(screenshots_dir, filename)
        try:
            stat = os.stat(screenshot_path)
            screenshot_candidates.append((stat.st_mtime, filename, screenshot_path))
        except OSError as e:
            logger.debug(f"读取截图文件信息失败 {screenshot_path}: {e}")

    if not screenshot_candidates:
        logger.warning("没有找到可读取的共享截图，将回退为实时截图")
        try:
            return capture_live_screenshot()
        except Exception as e:
            logger.error(f"实时截图失败: {e}")
            raise

    screenshot_candidates.sort(key=lambda item: item[0], reverse=True)
    latest_mtime, latest_screenshot, screenshot_path = screenshot_candidates[0]
    screenshot_age = max(0.0, time.time() - float(latest_mtime))

    if screenshot_age > 20:
        logger.warning(
            f"最新共享截图已过期 {screenshot_age:.1f} 秒: {screenshot_path}，将优先尝试实时截图"
        )
        try:
            return capture_live_screenshot()
        except Exception as e:
            logger.warning(f"实时截图失败，将回退到共享截图: {e}")

    logger.info(
        f"使用最新截图: {screenshot_path} (mtime={datetime.datetime.fromtimestamp(latest_mtime).isoformat(timespec='seconds')})"
    )

    try:
        with Image.open(screenshot_path) as screenshot:
            screenshot.load()
            return encode_image_to_jpeg_bytes(screenshot), f"共享截图:{latest_screenshot}"
    except Exception as e:
        logger.error(f"读取截图文件失败: {e}")
        try:
            return capture_live_screenshot()
        except Exception as e2:
            logger.error(f"实时截图失败: {e2}")
            raise


async def capture_screen_bytes(host: Any) -> tuple[bytes, str]:
    """返回截图字节流与来源标签（remote 或本地/共享目录）。"""
    if str(getattr(host, "capture_source", "local") or "").strip().lower() == "remote":
        from .screen_relay import capture_screen_bytes_remote

        return await capture_screen_bytes_remote(host)

    return await asyncio.to_thread(capture_screen_bytes_local_sync, host)
