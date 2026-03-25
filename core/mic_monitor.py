"""麦克风音量轮询与超过阈值时的临时识屏任务。"""

from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any

from astrbot.api import logger


def resolve_microphone_input_device(host: Any) -> tuple[int | None, dict | None]:
    """解析要使用的麦克风输入设备。"""
    try:
        import sounddevice as sd
    except ImportError:
        return None, None

    cached_index = getattr(host, "_mic_input_device_index", None)
    if cached_index is not None:
        try:
            info = sd.query_devices(int(cached_index), kind="input")
            if info and int(info.get("max_input_channels", 0) or 0) > 0:
                return int(cached_index), dict(info)
        except Exception:
            host._mic_input_device_index = None
            host._mic_input_device_name = ""

    candidates: list[tuple[int, dict]] = []

    try:
        default = sd.default.device
        default_input = None
        if isinstance(default, (tuple, list)) and len(default) >= 1:
            default_input = default[0]
        if default_input is not None and int(default_input) >= 0:
            info = sd.query_devices(int(default_input), kind="input")
            if info and int(info.get("max_input_channels", 0) or 0) > 0:
                candidates.append((int(default_input), dict(info)))
    except Exception:
        pass

    try:
        for idx, dev in enumerate(sd.query_devices() or []):
            try:
                if int(dev.get("max_input_channels", 0) or 0) <= 0:
                    continue
                if any(existing_idx == idx for existing_idx, _ in candidates):
                    continue
                candidates.append((idx, dict(dev)))
            except Exception:
                continue
    except Exception:
        pass

    if not candidates:
        return None, None

    device_index, info = candidates[0]
    host._mic_input_device_index = int(device_index)
    host._mic_input_device_name = str(info.get("name", "") or "")
    return int(device_index), info


def get_microphone_volume(host: Any) -> int:
    """读取当前麦克风音量（0–100）。"""
    host._ensure_runtime_state()
    try:
        import numpy as np

        import sounddevice as sd
        import soundfile  # noqa: F401

        device_index, device_info = resolve_microphone_input_device(host)
        if device_info is None:
            logger.warning("未找到可用的麦克风输入设备，已跳过本轮音量检测")
            return 0

        sample_rate = int(float(device_info.get("default_samplerate", 44100) or 44100))
        sample_rate = max(8000, sample_rate)
        frames_per_buffer = 2048

        chunks: list[Any] = []
        for chunk_index in range(4):
            data = sd.rec(
                frames=frames_per_buffer,
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                device=device_index,
                blocking=True,
            )
            if data is None:
                continue
            if chunk_index == 0:
                continue
            arr = np.asarray(data, dtype=np.float32).reshape(-1)
            if arr.size:
                chunks.append(arr)

        if not chunks:
            return 0

        audio_data = np.concatenate(chunks).astype(np.float32, copy=False)
        if audio_data.size == 0:
            return 0

        mean_square = float(np.mean(np.square(audio_data, dtype=np.float32), dtype=np.float64))
        if not np.isfinite(mean_square) or mean_square <= 0:
            return 0

        rms = float(np.sqrt(mean_square))
        if not np.isfinite(rms) or rms <= 0:
            return 0

        # sounddevice(float32) typically returns in [-1.0, 1.0]
        volume = min(100, max(0, int(rms * 100 * 5)))
        return volume
    except ImportError:
        logger.debug("麦克风监听依赖未安装，无法读取音量")
        return 0
    except Exception as e:
        logger.error(f"获取麦克风音量失败: {e}")
        return 0


def ensure_mic_monitor_background_task(host: Any) -> None:
    host._ensure_runtime_state()
    task = getattr(host, "_mic_monitor_background_task", None)
    if task and not task.done():
        return
    if not host.enable_mic_monitor or not host.running:
        return
    task = host._safe_create_task(mic_monitor_task(host), name="mic_monitor")
    host._mic_monitor_background_task = task
    if task not in host.background_tasks:
        host.background_tasks.append(task)


def stop_mic_monitor_background_task(host: Any) -> None:
    task = getattr(host, "_mic_monitor_background_task", None)
    if task and not task.done():
        task.cancel()
    host._mic_monitor_background_task = None


async def mic_monitor_task(host: Any) -> None:
    """后台麦克风监听循环。"""
    host._ensure_runtime_state()
    mic_deps_ok = False
    try:
        import sys

        logger.info(f"[麦克风依赖检查] Python 路径: {sys.path}")
        logger.info(f"[麦克风依赖检查] Python 可执行文件: {sys.executable}")

        import sounddevice as sd

        logger.info("[麦克风依赖检查] sounddevice 已加载")
        logger.info(f"[麦克风依赖检查] 默认音频设备: {getattr(sd, 'default', None)}")

        import soundfile

        logger.info(
            f"[麦克风依赖检查] soundfile 已加载: {getattr(soundfile, '__version__', '?')}"
        )

        import numpy

        logger.info(f"[麦克风依赖检查] NumPy 已加载: {numpy.__version__}")

        mic_deps_ok = True
    except ImportError as e:
        logger.warning(f"[麦克风依赖检查] 未安装麦克风监听所需依赖: {e}")
        logger.warning("请执行 pip install sounddevice soundfile numpy 以启用麦克风监听功能")
        logger.warning(f"[麦克风依赖检查] 详细错误: {traceback.format_exc()}")

    while host.enable_mic_monitor and host._is_current_process_instance():
        try:
            use_remote_mic = (
                str(getattr(host, "capture_source", "local") or "").strip().lower() == "remote"
            )
            if not use_remote_mic and not mic_deps_ok:
                await asyncio.sleep(60)
                continue

            current_time = time.time()

            if current_time - host.last_mic_trigger < host.mic_debounce_time:
                await asyncio.sleep(host.mic_check_interval)
                continue

            if use_remote_mic:
                volume = int(getattr(host, "_latest_remote_mic_level", 0) or 0)
            else:
                volume = get_microphone_volume(host)
            logger.debug(f"麦克风音量: {volume}")

            if volume > host.mic_threshold:
                logger.info(f"麦克风音量超过阈值: {volume} > {host.mic_threshold}")

                ok, err_msg = host._check_env(check_mic=True)
                if not ok:
                    logger.error(f"麦克风触发失败: {err_msg}")
                    await asyncio.sleep(host.mic_check_interval)
                    continue

                try:
                    current_state = host.state
                    restore_state = current_state
                    if current_state == "inactive":
                        host.state = "temporary"

                    temp_task_id = f"temp_mic_{int(time.time())}"

                    async def temp_mic_task(_restore_state: Any = restore_state) -> None:
                        background_job_started = False
                        try:
                            background_job_started, skip_reason = host._try_begin_background_screen_job()
                            if not background_job_started:
                                logger.info(f"[{temp_task_id}] 跳过麦克风触发识屏: {skip_reason}")
                                return
                            target = host._resolve_proactive_target()
                            event = host._create_virtual_event(target)

                            capture_timeout = host._get_capture_context_timeout(
                                "video" if host._use_screen_recording_mode() else "image"
                            )
                            capture_context = await asyncio.wait_for(
                                host._capture_proactive_recognition_context(),
                                timeout=capture_timeout,
                            )
                            active_window_title = capture_context.get("active_window_title", "")
                            components = await asyncio.wait_for(
                                host._analyze_screen(
                                    capture_context,
                                    session=event,
                                    active_window_title=active_window_title,
                                    custom_prompt="刚才那边好像有点动静？让我看看你现在在做什么呢。",
                                    task_id=temp_task_id,
                                ),
                                timeout=host._get_screen_analysis_timeout(
                                    capture_context.get("media_kind", "image")
                                ),
                            )

                            target = host._resolve_proactive_target()

                            if target and await host._send_component_text(
                                target,
                                components,
                                prefix="【声音提醒】",
                            ):
                                logger.info("麦克风提醒消息发送成功")
                                _mic_plain = host._extract_plain_text(components)
                                if str(_mic_plain or "").strip():
                                    host._remember_companion_outbound_for_umo(
                                        getattr(event, "unified_msg_origin", None),
                                        _mic_plain,
                                    )
                                if capture_context.get("_rest_reminder_planned"):
                                    host._mark_rest_reminder_sent(
                                        capture_context.get("_rest_reminder_info", {}) or {}
                                    )

                            host.last_mic_trigger = current_time
                        finally:
                            if temp_task_id in host.temporary_tasks:
                                del host.temporary_tasks[temp_task_id]
                            if background_job_started:
                                host._finish_background_screen_job()
                            if not host.auto_tasks and not host.temporary_tasks:
                                host.state = _restore_state

                    host.temporary_tasks[temp_task_id] = asyncio.create_task(temp_mic_task())
                    logger.info(f"已创建麦克风临时任务: {temp_task_id}")
                except Exception as e:
                    logger.error(f"创建麦克风临时任务时出错: {e}")
                    if not host.auto_tasks and not host.temporary_tasks:
                        host.state = restore_state

            await asyncio.sleep(host.mic_check_interval)
        except Exception as e:
            logger.error(f"麦克风监听任务异常: {e}")
            await asyncio.sleep(host.mic_check_interval)
