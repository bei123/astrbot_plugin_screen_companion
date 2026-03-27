#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capture host screenshots into a shared directory for Docker deployments.

This helper is intended to run on the host machine with a graphical desktop.
The plugin inside Docker can then read the generated files via the shared
directory mode.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


try:
    from PIL import Image, ImageGrab
except Exception:  # pragma: no cover - optional import fallback
    Image = None
    ImageGrab = None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Periodically capture host screenshots into a shared directory for "
            "astrbot_plugin_screen_companion running in Docker."
        )
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory used by the plugin shared screenshot mode.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between captures. Default: 5.",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=85,
        help="JPEG quality from 1 to 100. Default: 85.",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=120,
        help="How many timestamped screenshots to keep. Default: 120.",
    )
    parser.add_argument(
        "--prefix",
        default="screenshot",
        help="Screenshot filename prefix. Default: screenshot.",
    )
    parser.add_argument(
        "--latest-name",
        default="screenshot_latest.jpg",
        help="Latest screenshot filename. Default: screenshot_latest.jpg.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Capture one screenshot and exit.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print a log line for each successful capture.",
    )
    return parser


@dataclass
class CaptureConfig:
    output_dir: Path
    interval: float
    quality: int
    history_limit: int
    prefix: str
    latest_name: str
    once: bool
    verbose: bool


def _normalize_config(args: argparse.Namespace) -> CaptureConfig:
    interval = max(0.5, float(args.interval))
    quality = max(1, min(100, int(args.quality)))
    history_limit = max(1, int(args.history_limit))
    output_dir = Path(args.output_dir).expanduser().resolve()
    prefix = str(args.prefix or "screenshot").strip() or "screenshot"
    latest_name = str(args.latest_name or "screenshot_latest.jpg").strip() or "screenshot_latest.jpg"
    return CaptureConfig(
        output_dir=output_dir,
        interval=interval,
        quality=quality,
        history_limit=history_limit,
        prefix=prefix,
        latest_name=latest_name,
        once=bool(args.once),
        verbose=bool(args.verbose),
    )


def _require_display() -> None:
    if sys.platform.startswith("linux"):
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            raise RuntimeError(
                "Linux host has no DISPLAY or WAYLAND_DISPLAY. "
                "Run this helper inside a graphical desktop session."
            )


def _capture_image():
    _require_display()
    errors: list[str] = []

    if ImageGrab is not None:
        try:
            if sys.platform.startswith("win"):
                try:
                    image = ImageGrab.grab(all_screens=True)
                except TypeError:
                    image = ImageGrab.grab()
            else:
                image = ImageGrab.grab()
            if image is None:
                raise RuntimeError("ImageGrab returned no image.")
            return image.convert("RGB")
        except Exception as exc:  # pragma: no cover - environment-specific
            errors.append(f"Pillow ImageGrab failed: {exc}")

    try:
        import pyautogui

        image = pyautogui.screenshot()
        return image.convert("RGB")
    except Exception as exc:  # pragma: no cover - environment-specific
        errors.append(f"pyautogui screenshot failed: {exc}")

    raise RuntimeError(" ; ".join(errors) or "No screenshot backend available.")


def _atomic_save_jpeg(image, target_path: Path, quality: int) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    image.save(temp_path, format="JPEG", quality=quality)
    os.replace(temp_path, target_path)


def _iter_history_files(output_dir: Path, prefix: str, latest_name: str) -> Iterable[Path]:
    latest_basename = Path(latest_name).name
    pattern = f"{prefix}_*.jpg"
    for path in output_dir.glob(pattern):
        if path.name == latest_basename:
            continue
        if path.is_file():
            yield path


def _prune_history(output_dir: Path, prefix: str, latest_name: str, history_limit: int) -> None:
    history_files = sorted(
        _iter_history_files(output_dir, prefix, latest_name),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old_file in history_files[history_limit:]:
        try:
            old_file.unlink()
        except OSError:
            pass


def _copy_latest_if_needed(timestamp_path: Path, latest_path: Path) -> None:
    if timestamp_path == latest_path:
        return
    temp_path = latest_path.with_suffix(latest_path.suffix + ".tmp")
    shutil.copyfile(timestamp_path, temp_path)
    os.replace(temp_path, latest_path)


def _capture_once(config: CaptureConfig) -> Path:
    image = _capture_image()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    timestamp_name = f"{config.prefix}_{timestamp}.jpg"
    timestamp_path = config.output_dir / timestamp_name
    latest_path = config.output_dir / config.latest_name

    _atomic_save_jpeg(image, timestamp_path, config.quality)
    _copy_latest_if_needed(timestamp_path, latest_path)
    _prune_history(config.output_dir, config.prefix, config.latest_name, config.history_limit)
    return timestamp_path


def _log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    parser = _build_parser()
    config = _normalize_config(parser.parse_args())

    stop_requested = False

    def handle_stop(signum, _frame):
        nonlocal stop_requested
        stop_requested = True
        _log(f"Received signal {signum}, exiting screenshot bridge.")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handle_stop)
        except Exception:
            continue

    config.output_dir.mkdir(parents=True, exist_ok=True)
    _log(f"Writing screenshots to: {config.output_dir}")

    while not stop_requested:
        started_at = time.monotonic()
        try:
            screenshot_path = _capture_once(config)
            if config.verbose or config.once:
                _log(f"Captured: {screenshot_path.name}")
        except Exception as exc:
            _log(f"Capture failed: {exc}")
            if config.once:
                return 1

        if config.once:
            return 0

        elapsed = time.monotonic() - started_at
        sleep_seconds = max(0.1, config.interval - elapsed)
        time.sleep(sleep_seconds)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
