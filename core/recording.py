"""Desktop screen recording (Windows / ffmpeg gdigrab) and video sample sheets."""

from __future__ import annotations

import asyncio
import datetime
import io
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from astrbot.api import logger

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_DEFAULT_RECORDING_FPS = 1.0
_DEFAULT_RECORDING_DURATION = 10


def ensure_recording_runtime_state(host: Any) -> None:
    if not hasattr(host, "_screen_recording_lock") or host._screen_recording_lock is None:
        host._screen_recording_lock = asyncio.Lock()
    if not hasattr(host, "_screen_recording_process"):
        host._screen_recording_process = None
    if not hasattr(host, "_screen_recording_path"):
        host._screen_recording_path = ""
    if not hasattr(host, "_recording_audio_device"):
        host._recording_audio_device = None
    if not hasattr(host, "_recording_ffmpeg_path"):
        host._recording_ffmpeg_path = None
    if not hasattr(host, "_recording_video_encoder"):
        host._recording_video_encoder = None
    if not hasattr(host, "_recording_video_encoder_source"):
        host._recording_video_encoder_source = ""


def get_recording_fps(host: Any) -> float:
    fallback = float(getattr(host, "RECORDING_FPS", _DEFAULT_RECORDING_FPS) or _DEFAULT_RECORDING_FPS)
    return max(0.01, float(getattr(host, "recording_fps", fallback) or fallback))


def get_recording_duration_seconds(host: Any) -> int:
    fallback = int(
        getattr(host, "RECORDING_DURATION_SECONDS", _DEFAULT_RECORDING_DURATION)
        or _DEFAULT_RECORDING_DURATION
    )
    return max(
        1,
        int(getattr(host, "recording_duration_seconds", fallback) or fallback),
    )


def get_ffmpeg_storage_dir(host: Any, create: bool = False) -> str:
    data_dir = str(getattr(host.plugin_config, "data_dir", "") or "").strip()
    if data_dir:
        ffmpeg_dir = os.path.join(data_dir, "bin")
    else:
        ffmpeg_dir = os.path.join(_PLUGIN_ROOT, "bin")
    if create:
        os.makedirs(ffmpeg_dir, exist_ok=True)
    return ffmpeg_dir


def get_ffmpeg_path(host: Any) -> str:
    ensure_recording_runtime_state(host)
    cached_path = getattr(host, "_recording_ffmpeg_path", None)
    if cached_path and os.path.exists(cached_path):
        return cached_path

    candidate_paths: list[str] = []

    configured_path = str(getattr(host, "ffmpeg_path", "") or "").strip()
    if configured_path:
        candidate_paths.append(configured_path)

    data_ffmpeg_dir = get_ffmpeg_storage_dir(host)
    candidate_paths.extend(
        [
            os.path.join(data_ffmpeg_dir, "ffmpeg.exe"),
            os.path.join(data_ffmpeg_dir, "ffmpeg"),
            os.path.join(_PLUGIN_ROOT, "bin", "ffmpeg.exe"),
            os.path.join(_PLUGIN_ROOT, "bin", "ffmpeg"),
            os.path.join(_PLUGIN_ROOT, "ffmpeg.exe"),
            os.path.join(_PLUGIN_ROOT, "ffmpeg"),
        ]
    )

    for candidate in candidate_paths:
        normalized = os.path.abspath(os.path.expanduser(candidate))
        if os.path.isfile(normalized):
            host._recording_ffmpeg_path = normalized
            return normalized

    ffmpeg_path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe") or ""
    host._recording_ffmpeg_path = ffmpeg_path or None
    return ffmpeg_path


def get_recording_video_encoder(host: Any) -> str:
    ensure_recording_runtime_state(host)
    ffmpeg_path = get_ffmpeg_path(host)
    if not ffmpeg_path:
        return "libx264"

    cached_encoder = str(getattr(host, "_recording_video_encoder", "") or "").strip()
    cached_source = str(getattr(host, "_recording_video_encoder_source", "") or "").strip()
    if cached_encoder and cached_source == ffmpeg_path:
        return cached_encoder

    encoder = "mpeg4"
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=12,
            creationflags=creationflags,
        )
        output = "\n".join(
            piece for piece in [result.stdout or "", result.stderr or ""] if piece
        )
        if "libx264" in output:
            encoder = "libx264"
    except Exception as e:
        logger.debug(f"检测 ffmpeg 编码器失败，将使用兼容编码器: {e}")

    host._recording_video_encoder = encoder
    host._recording_video_encoder_source = ffmpeg_path
    return encoder


def build_recording_video_args(host: Any) -> list[str]:
    encoder = get_recording_video_encoder(host)
    args = ["-c:v", encoder]
    if encoder == "libx264":
        args.extend(["-preset", "ultrafast", "-crf", "32"])
    else:
        args.extend(["-q:v", "7"])
    args.extend(["-pix_fmt", "yuv420p"])
    return args


def build_evenly_spaced_indices(total_count: int, sample_count: int) -> list[int]:
    total = max(0, int(total_count or 0))
    target = max(1, int(sample_count or 1))
    if total <= 0:
        return []
    if total <= target:
        return list(range(total))
    if target == 1:
        return [total // 2]

    last_index = total - 1
    indices = []
    for position in range(target):
        ratio = position / max(1, target - 1)
        indices.append(int(round(last_index * ratio)))
    return sorted(set(max(0, min(last_index, value)) for value in indices))


def build_sample_frame_labels(total_count: int, chosen_indices: list[int]) -> list[str]:
    if not chosen_indices:
        return []
    if len(chosen_indices) == 1:
        return ["中段"]
    if len(chosen_indices) == 2:
        return ["开头", "结尾"]
    if len(chosen_indices) == 3:
        return ["开头", "中段", "结尾"]

    labels = []
    last_index = max(1, int(total_count) - 1)
    for index, frame_index in enumerate(chosen_indices):
        if index == 0:
            labels.append("开头")
            continue
        if index == len(chosen_indices) - 1:
            labels.append("结尾")
            continue
        percent = int(round((frame_index / last_index) * 100))
        labels.append(f"{percent}%")
    return labels


def get_video_sampling_plan(
    host: Any,
    scene: str,
    *,
    duration_seconds: int,
    use_external_vision: bool,
) -> dict[str, Any]:
    normalized_duration = max(
        1, int(duration_seconds or get_recording_duration_seconds(host))
    )
    profile = host._get_scene_behavior_profile(scene)
    category = str(profile.get("category", "general") or "general")

    if normalized_duration <= 8:
        sample_count = 3
    elif normalized_duration <= 15:
        sample_count = 4
    elif normalized_duration <= 25:
        sample_count = 5
    else:
        sample_count = 6

    if category == "entertainment":
        sample_count = min(6, sample_count + 1)
    elif category == "work":
        sample_count = max(3, sample_count - 1)

    if use_external_vision:
        sample_count = max(sample_count, 4)

    if sample_count <= 3:
        sampling_strategy = "keyframe_sheet"
    elif category == "entertainment":
        sampling_strategy = "timeline_sheet_dense"
    elif category == "work":
        sampling_strategy = "timeline_sheet_compact"
    else:
        sampling_strategy = "timeline_sheet"

    return {
        "sample_count": sample_count,
        "sampling_strategy": sampling_strategy,
        "duration_seconds": normalized_duration,
        "scene_category": category,
    }


def extract_video_sample_sheet_sync(
    host: Any,
    video_bytes: bytes,
    *,
    sample_count: int = 3,
    sampling_strategy: str = "keyframe_sheet",
    latest_frame_bytes: bytes | None = None,
) -> dict[str, Any] | None:
    ffmpeg_path = get_ffmpeg_path(host)
    if not ffmpeg_path or not video_bytes:
        return None

    from PIL import Image, ImageDraw, ImageFont

    with tempfile.TemporaryDirectory(prefix="screen_companion_sample_") as temp_dir:
        input_path = os.path.join(temp_dir, "input.mp4")
        with open(input_path, "wb") as f:
            f.write(video_bytes)

        frame_pattern = os.path.join(temp_dir, "frame_%03d.jpg")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                input_path,
                "-vf",
                "fps=1",
                frame_pattern,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=20,
            creationflags=creationflags,
        )
        if result.returncode != 0:
            return None

        frame_paths = sorted(
            os.path.join(temp_dir, filename)
            for filename in os.listdir(temp_dir)
            if filename.startswith("frame_") and filename.endswith(".jpg")
        )
        if not frame_paths:
            return None

        chosen_indices = build_evenly_spaced_indices(len(frame_paths), sample_count)
        chosen_paths = [frame_paths[index] for index in chosen_indices]
        frame_labels = build_sample_frame_labels(len(frame_paths), chosen_indices)
        frames = []
        for index, frame_path in enumerate(chosen_paths):
            with Image.open(frame_path) as image:
                frame = image.convert("RGB")
                label = frame_labels[min(index, len(frame_labels) - 1)]
                frames.append((label, frame.copy()))

        has_live_anchor_frame = False
        if latest_frame_bytes:
            try:
                with Image.open(io.BytesIO(latest_frame_bytes)) as latest_image:
                    frames.append(("现在", latest_image.convert("RGB").copy()))
                    has_live_anchor_frame = True
            except Exception:
                has_live_anchor_frame = False

        if not frames:
            return None

        target_width = min(960, max(frame.width for _, frame in frames))
        padding = 18
        gap = 12
        label_height = 34
        resized_frames = []
        for label, frame in frames:
            scale = target_width / max(1, frame.width)
            target_height = max(1, int(frame.height * scale))
            resized_frames.append(
                (
                    label,
                    frame.resize((target_width, target_height)),
                )
            )

        total_height = padding * 2 + sum(
            frame.height + label_height for _, frame in resized_frames
        ) + gap * max(0, len(resized_frames) - 1)
        canvas = Image.new("RGB", (target_width + padding * 2, total_height), "#111418")
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("msyh.ttc", 18)
        except Exception:
            font = ImageFont.load_default()

        current_y = padding
        for label, frame in resized_frames:
            draw.rounded_rectangle(
                (padding, current_y, padding + target_width, current_y + label_height - 8),
                radius=10,
                fill="#1d232c",
            )
            draw.text(
                (padding + 12, current_y + 5),
                f"{label}关键帧",
                fill="#f4f7fb",
                font=font,
            )
            current_y += label_height
            canvas.paste(frame, (padding, current_y))
            current_y += frame.height + gap

        buffer = io.BytesIO()
        canvas.save(buffer, format="JPEG", quality=86)
        return {
            "media_kind": "image",
            "mime_type": "image/jpeg",
            "media_bytes": buffer.getvalue(),
            "frame_count": len(resized_frames),
            "frame_labels": [label for label, _ in resized_frames],
            "sampling_strategy": sampling_strategy,
            "has_live_anchor_frame": has_live_anchor_frame,
        }


async def build_video_sample_capture_context(
    host: Any,
    capture_context: dict[str, Any],
    *,
    scene: str,
    use_external_vision: bool,
) -> dict[str, Any] | None:
    media_bytes = capture_context.get("media_bytes", b"") or b""
    duration_seconds = int(
        capture_context.get("duration_seconds", 0) or get_recording_duration_seconds(host)
    )
    sampling_plan = get_video_sampling_plan(
        host,
        scene,
        duration_seconds=duration_seconds,
        use_external_vision=use_external_vision,
    )
    sample_sheet = await asyncio.to_thread(
        extract_video_sample_sheet_sync,
        host,
        media_bytes,
        sample_count=int(sampling_plan.get("sample_count", 3) or 3),
        sampling_strategy=str(
            sampling_plan.get("sampling_strategy", "keyframe_sheet") or "keyframe_sheet"
        ),
        latest_frame_bytes=capture_context.get("latest_image_bytes", b"") or None,
    )
    if not sample_sheet:
        return None

    return {
        "media_kind": "image",
        "mime_type": sample_sheet["mime_type"],
        "media_bytes": sample_sheet["media_bytes"],
        "active_window_title": capture_context.get("active_window_title", ""),
        "source_label": "录屏关键帧拼图",
        "sampling_strategy": sample_sheet.get("sampling_strategy", "keyframe_sheet"),
        "frame_count": sample_sheet.get("frame_count", 0),
        "frame_labels": sample_sheet.get("frame_labels", []),
        "has_live_anchor_frame": bool(sample_sheet.get("has_live_anchor_frame")),
        "duration_seconds": duration_seconds,
        "original_media_kind": "video",
    }


def should_keep_sampled_video_only(
    host: Any,
    scene: str,
    *,
    use_external_vision: bool,
    preserve_full_video_for_audio: bool = False,
) -> bool:
    if preserve_full_video_for_audio:
        return False
    profile = host._get_scene_behavior_profile(scene)
    if use_external_vision:
        return True
    return bool(profile.get("prefer_sample_only", False))


def get_recording_cache_dir(host: Any) -> str:
    cache_dir = os.path.join(str(host.plugin_config.data_dir), "cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def detect_system_audio_device(host: Any) -> str | None:
    if sys.platform != "win32":
        return None
    if host._recording_audio_device is not None:
        return host._recording_audio_device

    ffmpeg_path = get_ffmpeg_path(host)
    if not ffmpeg_path:
        host._recording_audio_device = ""
        return host._recording_audio_device

    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-list_devices",
        "true",
        "-f",
        "dshow",
        "-i",
        "dummy",
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=10,
            creationflags=creationflags,
        )
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
    except Exception as e:
        logger.debug(f"检测系统音频设备失败: {e}")
        host._recording_audio_device = ""
        return host._recording_audio_device

    keywords = ("立体声混音", "stereo mix", "realtek")
    matched_devices: list[str] = []
    for line in output.splitlines():
        lower_line = line.lower()
        if not any(keyword in lower_line for keyword in keywords):
            continue
        match = re.search(r'"([^"]+)"', line)
        if match:
            matched_devices.append(match.group(1))

    host._recording_audio_device = matched_devices[0] if matched_devices else ""
    if host._recording_audio_device:
        logger.info(f"检测到系统音频设备: {host._recording_audio_device}")
    else:
        logger.info("未检测到可用的系统音频设备，将仅录制桌面画面")
    return host._recording_audio_device


def cleanup_recording_cache(host: Any, keep_latest: int = 3) -> None:
    try:
        cache_dir = get_recording_cache_dir(host)
        candidates = []
        for filename in os.listdir(cache_dir):
            if not filename.startswith("rec_") or not filename.endswith(".mp4"):
                continue
            path = os.path.join(cache_dir, filename)
            try:
                candidates.append((os.path.getmtime(path), path))
            except OSError:
                continue
        candidates.sort(key=lambda item: item[0], reverse=True)
        for _, path in candidates[keep_latest:]:
            try:
                os.remove(path)
            except OSError:
                pass
    except Exception as e:
        logger.debug(f"清理录屏缓存失败: {e}")


def record_screen_clip_sync(host: Any, duration_seconds: int) -> str:
    ffmpeg_path = get_ffmpeg_path(host)
    if not ffmpeg_path:
        raise RuntimeError(
            "\u672a\u627e\u5230 ffmpeg\uff0c\u8bf7\u5c06 ffmpeg.exe \u653e\u5230\u63d2\u4ef6\u76ee\u5f55\u4e0b\u7684 bin \u6587\u4ef6\u5939\uff0c"
            "\u6216\u5728\u914d\u7f6e\u4e2d\u586b\u5199 ffmpeg_path\uff0c\u6216\u52a0\u5165 PATH\u3002"
        )
    if sys.platform != "win32":
        raise RuntimeError(
            "\u5f55\u5c4f\u89c6\u9891\u8bc6\u522b\u76ee\u524d\u4ec5\u652f\u6301 Windows \u684c\u9762\u73af\u5883\u3002"
        )

    duration = max(1, int(duration_seconds or get_recording_duration_seconds(host)))
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    clip_name = f"manual_rec_{timestamp}_{secrets.token_hex(4)}.mp4"
    output_path = os.path.join(get_recording_cache_dir(host), clip_name)
    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "gdigrab",
        "-framerate",
        str(get_recording_fps(host)),
        "-i",
        "desktop",
    ]

    audio_device = detect_system_audio_device(host)
    if audio_device:
        cmd.extend(
            [
                "-f",
                "dshow",
                "-i",
                f"audio={audio_device}",
                "-shortest",
            ]
        )

    cmd.extend(
        [
            "-t",
            str(duration),
        ]
    )
    cmd.extend(build_recording_video_args(host))
    cmd.append(output_path)

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=max(duration + 30, 45),
        creationflags=creationflags,
    )
    if result.returncode != 0:
        stderr_text = (result.stderr or "").strip()
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        raise RuntimeError(
            "\u5355\u6b21\u5f55\u5c4f\u5931\u8d25\uff0cffmpeg \u5df2\u9000\u51fa\u3002"
            + (f" stderr: {stderr_text[:300]}" if stderr_text else "")
        )
    return output_path


def start_screen_recording_sync(host: Any) -> str:
    ffmpeg_path = get_ffmpeg_path(host)
    if not ffmpeg_path:
        raise RuntimeError(
            "未找到 ffmpeg，请将 ffmpeg.exe 放到插件数据目录下的 bin 文件夹，"
            "或在配置中填写 ffmpeg_path，或加入 PATH。"
        )
    if sys.platform != "win32":
        raise RuntimeError("录屏视频识别目前仅支持 Windows 桌面环境")

    process = getattr(host, "_screen_recording_process", None)
    if process and process.poll() is None:
        return str(getattr(host, "_screen_recording_path", "") or "")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(get_recording_cache_dir(host), f"rec_{timestamp}.mp4")
    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "gdigrab",
        "-framerate",
        str(get_recording_fps(host)),
        "-i",
        "desktop",
    ]

    audio_device = detect_system_audio_device(host)
    if audio_device:
        cmd.extend(
            [
                "-f",
                "dshow",
                "-i",
                f"audio={audio_device}",
                "-shortest",
            ]
        )

    cmd.extend(
        [
            "-t",
            str(get_recording_duration_seconds(host)),
        ]
    )
    cmd.extend(build_recording_video_args(host))
    cmd.append(output_path)

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    host._screen_recording_process = process
    host._screen_recording_path = output_path
    cleanup_recording_cache(host)
    logger.info(f"已启动桌面录屏: {output_path}")
    return output_path


def stop_screen_recording_sync(host: Any) -> str:
    process = getattr(host, "_screen_recording_process", None)
    output_path = str(getattr(host, "_screen_recording_path", "") or "")
    host._screen_recording_process = None
    host._screen_recording_path = ""

    if process and process.poll() is None:
        try:
            if process.stdin:
                process.stdin.write(b"q\n")
                process.stdin.flush()
        except Exception:
            pass

        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    return output_path


async def ensure_recording_ready(host: Any) -> None:
    ensure_recording_runtime_state(host)
    async with host._screen_recording_lock:
        await asyncio.to_thread(start_screen_recording_sync, host)


async def stop_recording_if_running(host: Any) -> None:
    ensure_recording_runtime_state(host)
    async with host._screen_recording_lock:
        await asyncio.to_thread(stop_screen_recording_sync, host)
