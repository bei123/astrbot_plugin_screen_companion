"""桌面环境检查与截图/录屏采集上下文构建。"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from typing import Any

from astrbot.api import logger
from astrbot.api.star import StarTools


def check_dependencies(sc: Any, check_mic: bool = False) -> tuple[bool, str]:
    """检查可选运行时依赖。"""
    sc._ensure_runtime_state()
    missing_libs: list[str] = []
    if sc._use_screen_recording_mode():
        if not sc._get_ffmpeg_path():
            missing_libs.append("ffmpeg")
    elif str(getattr(sc, "capture_source", "local") or "").strip().lower() != "remote":
        try:
            import pyautogui  # noqa: F401
        except ImportError:
            missing_libs.append("pyautogui")

        try:
            from PIL import Image as PILImage  # noqa: F401
        except ImportError:
            missing_libs.append("Pillow")

    if (
        sys.platform == "win32"
        and sc.capture_active_window
        and not sc._use_screen_recording_mode()
        and str(getattr(sc, "capture_source", "local") or "").strip().lower() != "remote"
    ):
        try:
            import pygetwindow  # noqa: F401
        except ImportError:
            missing_libs.append("pygetwindow")

    if (
        check_mic
        and sc.enable_mic_monitor
        and str(getattr(sc, "capture_source", "local") or "").strip().lower() != "remote"
    ):
        try:
            import sounddevice  # noqa: F401
        except ImportError:
            missing_libs.append("sounddevice")

        try:
            import numpy  # noqa: F401
        except ImportError:
            missing_libs.append("numpy")

    if missing_libs:
        if missing_libs == ["ffmpeg"]:
            return (
                False,
                "缺少 ffmpeg。你可以将 ffmpeg.exe 放到插件数据目录下的 bin 文件夹，"
                "或在配置中填写 ffmpeg_path，或加入系统 PATH。",
            )
        return (
            False,
            f"缺少必要依赖库: {', '.join(missing_libs)}。请执行: pip install {' '.join(missing_libs)}",
        )
    return True, ""


def check_env(sc: Any, check_mic: bool = False) -> tuple[bool, str]:
    """检查桌面环境是否可用于截图/录屏。"""
    dep_ok, dep_msg = check_dependencies(sc, check_mic=check_mic)
    if not dep_ok:
        return False, dep_msg

    if sc._use_screen_recording_mode():
        if sys.platform != "win32":
            return False, "录屏视频识别目前仅支持 Windows 桌面环境。"
        ffmpeg_path = sc._get_ffmpeg_path()
        if not ffmpeg_path:
            return (
                False,
                "未检测到 ffmpeg。请将 ffmpeg.exe 放到插件数据目录下的 bin 文件夹，"
                "或在配置中填写 ffmpeg_path，或加入系统 PATH。",
            )
        return True, ""

    if str(getattr(sc, "capture_source", "local") or "").strip().lower() == "remote":
        return True, ""

    try:
        import pyautogui

        if sys.platform.startswith("linux"):
            if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
                return (
                    False,
                    "Detected Linux without an available graphical display. Please run it in a desktop session or with X11 forwarding.",
                )

        size = pyautogui.size()
        if size[0] <= 0 or size[1] <= 0:
            return False, "Unable to capture the screen properly."

        return True, ""
    except Exception as e:
        return False, f"自我检查失败: {str(e)}"


def check_recording_env(sc: Any, check_mic: bool = False) -> tuple[bool, str]:
    dep_ok, dep_msg = check_dependencies(sc, check_mic=check_mic)
    if not dep_ok:
        return False, dep_msg

    if sys.platform != "win32":
        return False, "录屏视频识别目前仅支持 Windows 桌面环境。"

    ffmpeg_path = sc._get_ffmpeg_path()
    if not ffmpeg_path:
        return (
            False,
            "未检测到 ffmpeg，请将 ffmpeg.exe 放到插件目录下的 bin 文件夹，"
            "或在配置中填写 ffmpeg_path，或加入 PATH。",
        )

    return True, ""


def check_screenshot_env(sc: Any, check_mic: bool = False) -> tuple[bool, str]:
    dep_ok, dep_msg = check_dependencies(sc, check_mic=check_mic)
    if not dep_ok and "ffmpeg" not in str(dep_msg or "").lower():
        return False, dep_msg

    if str(getattr(sc, "capture_source", "local") or "").strip().lower() == "remote":
        return True, ""

    try:
        import pyautogui

        if sys.platform.startswith("linux"):
            if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
                return (
                    False,
                    "Detected Linux without an available graphical display. Please run it in a desktop session or with X11 forwarding.",
                )

        size = pyautogui.size()
        if size[0] <= 0 or size[1] <= 0:
            return False, "Unable to capture the screen properly."

        return True, ""
    except Exception as e:
        if sc._get_runtime_flag("use_shared_screenshot_dir"):
            shared_dir = str(getattr(sc, "shared_screenshot_dir", "") or "").strip()
            if shared_dir:
                return True, ""
        return False, f"自我检查失败: {str(e)}"


async def capture_screenshot_context(sc: Any) -> dict[str, Any]:
    image_bytes, active_window_title = await sc._capture_screen_bytes()
    return {
        "media_kind": "image",
        "mime_type": "image/jpeg",
        "media_bytes": image_bytes,
        "active_window_title": active_window_title,
        "source_label": active_window_title,
    }


async def capture_latest_screen_anchor(
    sc: Any,
    *,
    fallback_window_title: str = "",
) -> tuple[bytes, str, str]:
    latest_image_bytes = b""
    latest_window_title = ""
    active_window_title = sc._normalize_window_title(fallback_window_title)
    try:
        latest_image_bytes, latest_window_title = await sc._capture_screen_bytes()
        active_window_title = (
            sc._normalize_window_title(latest_window_title) or active_window_title
        )
    except Exception as e:
        logger.debug(f"录屏后补抓当前截图失败: {e}")
    return latest_image_bytes, latest_window_title, active_window_title


async def capture_recording_context(sc: Any) -> dict[str, Any]:
    sc._ensure_recording_runtime_state()
    clip_active_window_title, _ = await asyncio.to_thread(sc._get_active_window_info)

    async with sc._screen_recording_lock:
        current_path = str(getattr(sc, "_screen_recording_path", "") or "")
        current_process = getattr(sc, "_screen_recording_process", None)
        if not current_path:
            await asyncio.to_thread(sc._start_screen_recording_sync)
            await asyncio.sleep(1.5)
            current_path = str(getattr(sc, "_screen_recording_path", "") or "")
            current_process = getattr(sc, "_screen_recording_process", None)

        if current_process and current_process.poll() is None:
            video_path = await asyncio.to_thread(sc._stop_screen_recording_sync)
        else:
            video_path = current_path

        if not video_path or not os.path.exists(video_path):
            await asyncio.to_thread(sc._start_screen_recording_sync)
            raise RuntimeError("录屏文件尚未准备好，请稍后再试一次。")

        def _read_video_bytes() -> bytes:
            with open(video_path, "rb") as f:
                return f.read()

        video_bytes = await asyncio.to_thread(_read_video_bytes)
        if not video_bytes:
            await asyncio.to_thread(sc._start_screen_recording_sync)
            raise RuntimeError("录屏文件为空，请稍后再试一次。")

        if sc.save_local:
            try:
                data_dir = StarTools.get_data_dir()
                data_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(video_path, str(data_dir / "screen_record_latest.mp4"))
            except Exception as e:
                logger.error(f"保存录屏文件失败: {e}")

        await asyncio.to_thread(sc._start_screen_recording_sync)
        await asyncio.to_thread(sc._cleanup_recording_cache)

    latest_image_bytes, latest_window_title, active_window_title = await capture_latest_screen_anchor(
        sc,
        fallback_window_title=clip_active_window_title,
    )
    return {
        "media_kind": "video",
        "mime_type": "video/mp4",
        "media_bytes": video_bytes,
        "active_window_title": active_window_title,
        "clip_active_window_title": clip_active_window_title,
        "latest_window_title": latest_window_title,
        "latest_image_bytes": latest_image_bytes,
        "duration_seconds": sc._get_recording_duration_seconds(),
        "source_label": active_window_title or "最近一段桌面录屏",
    }


async def capture_one_shot_recording_context(
    sc: Any, duration_seconds: int | None = None
) -> dict[str, Any]:
    sc._ensure_recording_runtime_state()
    clip_active_window_title, _ = await asyncio.to_thread(sc._get_active_window_info)
    duration = max(1, int(duration_seconds or sc._get_recording_duration_seconds()))

    async with sc._screen_recording_lock:
        await asyncio.to_thread(sc._stop_screen_recording_sync)
        video_path = await asyncio.to_thread(sc._record_screen_clip_sync, duration)

    try:
        def _read_video_bytes() -> bytes:
            with open(video_path, "rb") as f:
                return f.read()

        video_bytes = await asyncio.to_thread(_read_video_bytes)
        if not video_bytes:
            raise RuntimeError("单次录屏文件为空，请稍后再试一次。")

        if sc.save_local:
            try:
                data_dir = StarTools.get_data_dir()
                data_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(video_path, str(data_dir / "screen_record_latest.mp4"))
            except Exception as e:
                logger.error(f"保存单次录屏文件失败: {e}")

        latest_image_bytes, latest_window_title, active_window_title = await capture_latest_screen_anchor(
            sc,
            fallback_window_title=clip_active_window_title,
        )

        return {
            "media_kind": "video",
            "mime_type": "video/mp4",
            "media_bytes": video_bytes,
            "active_window_title": active_window_title,
            "clip_active_window_title": clip_active_window_title,
            "latest_window_title": latest_window_title,
            "latest_image_bytes": latest_image_bytes,
            "duration_seconds": duration,
            "source_label": active_window_title or "手动录制的最近 10 秒桌面录屏",
        }
    finally:
        try:
            if os.path.exists(video_path):
                os.remove(video_path)
        except OSError:
            pass


async def capture_recognition_context(sc: Any) -> dict[str, Any]:
    if sc._use_screen_recording_mode():
        return await capture_recording_context(sc)

    return await capture_screenshot_context(sc)


async def capture_proactive_recognition_context(sc: Any) -> dict[str, Any]:
    if sc._use_screen_recording_mode():
        return await capture_one_shot_recording_context(sc, sc._get_recording_duration_seconds())

    return await capture_screenshot_context(sc)
