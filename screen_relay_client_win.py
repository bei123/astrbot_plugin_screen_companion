#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
屏幕伴侣 Windows 端推送程序（图形界面）。
将本机屏幕截图与可选麦克风音量推送到 Linux 服务器上的「屏幕伴侣」插件。

依赖（Windows）:
  pip install pyautogui Pillow pygetwindow
  启用麦克风时: pip install pyaudio numpy
"""

import io
import struct
import sys
import threading
import time

if sys.platform != "win32":
    print("本程序仅支持在 Windows 上运行。")
    sys.exit(1)


def _check_deps():
    missing = []
    try:
        import pyautogui
        from PIL import Image
    except ImportError:
        missing.append("pyautogui, Pillow")
    try:
        import pygetwindow
    except ImportError:
        missing.append("pygetwindow")
    return missing


def _check_mic_deps():
    try:
        import pyaudio
        import numpy
        return True
    except ImportError:
        return False


def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext

    _has_pygetwindow = "pygetwindow" not in _check_deps()
    _has_mic_libs = _check_mic_deps()
    base_missing = _check_deps()
    if base_missing:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("依赖缺失", "请先安装: pip install pyautogui Pillow pygetwindow\n\n缺少: " + ", ".join(base_missing))
        sys.exit(1)

    app = tk.Tk()
    app.title("屏幕伴侣 - Windows 端推送")
    app.minsize(420, 380)
    app.resizable(True, True)

    # 状态与控制
    stop_event = threading.Event()
    worker_socket = None
    worker_thread = None
    log_lines = []

    def log(msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        log_lines.append(line)
        if len(log_lines) > 200:
            log_lines.pop(0)

        def _append():
            try:
                log_text.config(state=tk.NORMAL)
                log_text.insert(tk.END, line + "\n")
                log_text.see(tk.END)
                log_text.config(state=tk.DISABLED)
            except Exception:
                pass

        app.after(0, _append)

    # 顶部配置区
    frame_cfg = ttk.LabelFrame(app, text="连接与推送设置", padding=8)
    frame_cfg.pack(fill=tk.X, padx=8, pady=6)

    row = 0
    ttk.Label(frame_cfg, text="服务器地址:").grid(row=row, column=0, sticky=tk.W, padx=(0, 4), pady=2)
    var_host = tk.StringVar(value="127.0.0.1")
    entry_host = ttk.Entry(frame_cfg, textvariable=var_host, width=18)
    entry_host.grid(row=row, column=1, sticky=tk.W, pady=2)
    ttk.Label(frame_cfg, text="端口:").grid(row=row, column=2, sticky=tk.W, padx=(12, 4), pady=2)
    var_port = tk.StringVar(value="8765")
    entry_port = ttk.Entry(frame_cfg, textvariable=var_port, width=6)
    entry_port.grid(row=row, column=3, sticky=tk.W, pady=2)
    row += 1

    ttk.Label(frame_cfg, text="截图间隔(秒):").grid(row=row, column=0, sticky=tk.W, padx=(0, 4), pady=2)
    var_interval = tk.StringVar(value="3")
    entry_interval = ttk.Entry(frame_cfg, textvariable=var_interval, width=6)
    entry_interval.grid(row=row, column=1, sticky=tk.W, pady=2)
    ttk.Label(frame_cfg, text="截图质量(1-100):").grid(row=row, column=2, sticky=tk.W, padx=(12, 4), pady=2)
    var_quality = tk.StringVar(value="70")
    entry_quality = ttk.Entry(frame_cfg, textvariable=var_quality, width=6)
    entry_quality.grid(row=row, column=3, sticky=tk.W, pady=2)
    row += 1

    var_window_only = tk.BooleanVar(value=False)
    cb_window = ttk.Checkbutton(frame_cfg, text="仅截取当前活动窗口", variable=var_window_only)
    cb_window.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=2)
    if not _has_pygetwindow:
        cb_window.config(state=tk.DISABLED)
        ttk.Label(frame_cfg, text="(需安装 pygetwindow)").grid(row=row, column=2, columnspan=2, sticky=tk.W, pady=2)
    row += 1

    var_mic = tk.BooleanVar(value=False)
    cb_mic = ttk.Checkbutton(frame_cfg, text="启用麦克风推送（实时音量）", variable=var_mic)
    cb_mic.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=2)
    if not _has_mic_libs:
        cb_mic.config(state=tk.DISABLED)
        ttk.Label(frame_cfg, text="(需安装 pyaudio numpy)").grid(row=row, column=2, columnspan=2, sticky=tk.W, pady=2)
    row += 1

    ttk.Label(frame_cfg, text="麦克风采样(次/秒):").grid(row=row, column=0, sticky=tk.W, padx=(0, 4), pady=2)
    var_mic_rate = tk.StringVar(value="10")
    entry_mic_rate = ttk.Entry(frame_cfg, textvariable=var_mic_rate, width=6)
    entry_mic_rate.grid(row=row, column=1, sticky=tk.W, pady=2)
    row += 1

    # 按钮与状态
    frame_btn = ttk.Frame(app)
    frame_btn.pack(fill=tk.X, padx=8, pady=4)
    var_status = tk.StringVar(value="未连接")
    label_status = ttk.Label(frame_btn, textvariable=var_status)
    label_status.pack(side=tk.LEFT)
    btn_start = ttk.Button(frame_btn, text="开始推送")
    btn_stop = ttk.Button(frame_btn, text="停止推送", state=tk.DISABLED)

    def get_params():
        try:
            port = int(var_port.get().strip())
            interval = float(var_interval.get().strip())
            quality = int(var_quality.get().strip())
            mic_rate = float(var_mic_rate.get().strip())
        except ValueError:
            return None
        return {
            "host": var_host.get().strip() or "127.0.0.1",
            "port": max(1, min(65535, port)),
            "interval": max(0.5, min(60, interval)),
            "quality": max(1, min(100, quality)),
            "window_only": var_window_only.get(),
            "mic": var_mic.get() and _has_mic_libs,
            "mic_rate": max(1, min(50, mic_rate)),
        }

    def worker():
        nonlocal worker_socket
        params = get_params()
        if not params:
            app.after(0, lambda: (var_status.set("参数无效"), log("参数无效，请检查数字格式")))
            app.after(0, lambda: (btn_start.config(state=tk.NORMAL), btn_stop.config(state=tk.DISABLED)))
            return
        host, port = params["host"], params["port"]
        interval = params["interval"]
        quality = params["quality"]
        window_only = params["window_only"]
        use_mic = params["mic"]
        mic_rate = params["mic_rate"]
        mic_interval = 1.0 / mic_rate if use_mic else interval

        import pyautogui
        from PIL import Image

        def _get_active_window_title():
            if not _has_pygetwindow:
                return ""
            try:
                import pygetwindow as gw
                w = gw.getActiveWindow()
                return (w.title or "").strip() if w else ""
            except Exception:
                return ""

        def capture_once():
            screenshot = None
            active_window_title = ""
            if window_only and _has_pygetwindow:
                try:
                    import pygetwindow as gw
                    w = gw.getActiveWindow()
                    if w and w.width > 0 and w.height > 0:
                        active_window_title = (w.title or "").strip()
                        screenshot = pyautogui.screenshot(
                            region=(int(w.left), int(w.top), int(w.width), int(w.height))
                        )
                except Exception:
                    pass
            if screenshot is None:
                screenshot = pyautogui.screenshot()
                active_window_title = _get_active_window_title()
            if screenshot.mode != "RGB":
                screenshot = screenshot.convert("RGB")
            buf = io.BytesIO()
            screenshot.save(buf, format="JPEG", quality=quality)
            return buf.getvalue(), active_window_title

        def get_mic_volume(stream_and_p=None):
            if not use_mic:
                return 0
            try:
                import pyaudio
                import numpy as np
                if stream_and_p is not None:
                    stream, _ = stream_and_p
                    data = stream.read(1024, exception_on_overflow=False)
                else:
                    p = pyaudio.PyAudio()
                    stream = p.open(
                        format=pyaudio.paInt16, channels=1, rate=44100,
                        input=True, frames_per_buffer=1024,
                    )
                    data = stream.read(1024)
                    stream.stop_stream()
                    stream.close()
                    p.terminate()
                audio_data = np.frombuffer(data, dtype=np.int16)
                if audio_data.size == 0:
                    return 0
                sq = np.square(audio_data.astype(np.float64))
                mean_sq = np.nanmean(sq)
                if not (mean_sq > 0 and np.isfinite(mean_sq)):
                    return 0
                rms = np.sqrt(mean_sq)
                return min(100, int(rms / 32768 * 100 * 5))
            except Exception:
                return 0

        def send_frame(sock, image_bytes: bytes, title: str, mic_level: int = 0):
            title_bytes = title.encode("utf-8", errors="replace")
            title_len = len(title_bytes)
            if use_mic:
                sock.sendall(b"\xFF\x01")
                sock.sendall(struct.pack(">I", title_len)[1:4])
                sock.sendall(title_bytes)
                sock.sendall(struct.pack(">I", len(image_bytes)))
                sock.sendall(image_bytes)
                sock.sendall(bytes([min(100, max(0, mic_level))]))
            else:
                sock.sendall(struct.pack(">I", title_len))
                sock.sendall(title_bytes)
                sock.sendall(struct.pack(">I", len(image_bytes)))
                sock.sendall(image_bytes)

        import socket as sock_module
        while not stop_event.is_set():
            try:
                s = sock_module.socket(sock_module.AF_INET, sock_module.SOCK_STREAM)
                s.settimeout(10)
                worker_socket = s
                s.connect((host, port))
                s.settimeout(None)
                app.after(0, lambda: (var_status.set("已连接，正在推送"), log("已连接，开始推送截图" + (" + 麦克风" if use_mic else ""))))
                stream_and_p = None
                if use_mic:
                    try:
                        import pyaudio
                        p = pyaudio.PyAudio()
                        stream = p.open(
                            format=pyaudio.paInt16, channels=1, rate=44100,
                            input=True, frames_per_buffer=1024,
                        )
                        stream_and_p = (stream, p)
                    except Exception as e:
                        app.after(0, lambda e=e: log(f"麦克风打开失败: {e}"))
                last_frame_time = time.time()
                try:
                    while not stop_event.is_set():
                        now = time.time()
                        if now - last_frame_time >= interval:
                            image_bytes, title = capture_once()
                            mic_level = get_mic_volume(stream_and_p) if use_mic else 0
                            send_frame(s, image_bytes, title, mic_level)
                            last_frame_time = now
                        elif use_mic and stream_and_p is not None:
                            mic_level = get_mic_volume(stream_and_p)
                            s.sendall(b"\xFE" + bytes([min(100, max(0, mic_level))]))
                        sleep_time = min(mic_interval, interval - (now - last_frame_time)) if use_mic else (interval - (now - last_frame_time))
                        sleep_time = max(0.01, min(sleep_time, interval))
                        end = time.time() + sleep_time
                        while end > time.time() and not stop_event.is_set():
                            time.sleep(0.05)
                finally:
                    worker_socket = None
                    if stream_and_p is not None:
                        stream, p = stream_and_p
                        try:
                            stream.stop_stream()
                            stream.close()
                        except Exception:
                            pass
                        try:
                            p.terminate()
                        except Exception:
                            pass
                    try:
                        s.close()
                    except Exception:
                        pass
            except Exception as e:
                worker_socket = None
                app.after(0, lambda e=e: (var_status.set("连接断开，重连中…"), log(f"连接或发送失败: {e}，将重连")))
            if stop_event.is_set():
                break
            time.sleep(1)
        app.after(0, lambda: (var_status.set("已停止"), btn_start.config(state=tk.NORMAL), btn_stop.config(state=tk.DISABLED), log("已停止推送")))

    def on_start():
        p = get_params()
        if p is None:
            messagebox.showwarning("参数错误", "请正确填写服务器地址、端口、间隔等（数字格式）。")
            return
        stop_event.clear()
        btn_start.config(state=tk.DISABLED)
        btn_stop.config(state=tk.NORMAL)
        var_status.set("正在连接…")
        log(f"连接 {p['host']}:{p['port']}，截图间隔 {p['interval']} 秒" + (f"，麦克风 {p['mic_rate']} 次/秒" if p['mic'] else ""))
        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()

    def on_stop():
        stop_event.set()
        if worker_socket is not None:
            try:
                worker_socket.close()
            except Exception:
                pass
        btn_stop.config(state=tk.DISABLED)
        var_status.set("正在停止…")

    btn_start.config(command=on_start)
    btn_stop.config(command=on_stop)
    btn_start.pack(side=tk.RIGHT, padx=4)
    btn_stop.pack(side=tk.RIGHT)

    # 日志区
    frame_log = ttk.LabelFrame(app, text="运行日志", padding=4)
    frame_log.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
    log_text = scrolledtext.ScrolledText(frame_log, height=8, state=tk.DISABLED, wrap=tk.WORD, font=("Consolas", 9))
    log_text.pack(fill=tk.BOTH, expand=True)
    log("就绪。填写服务器地址与端口后点击「开始推送」。")

    def on_closing():
        if btn_stop.cget("state") != tk.DISABLED:
            stop_event.set()
            if worker_socket is not None:
                try:
                    worker_socket.close()
                except Exception:
                    pass
        app.destroy()

    app.protocol("WM_DELETE_WINDOW", on_closing)
    app.mainloop()


if __name__ == "__main__":
    run_gui()
