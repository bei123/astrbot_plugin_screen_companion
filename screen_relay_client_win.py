#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
屏幕伴侣 Windows 端推送程序（图形界面）。
将本机屏幕截图与可选麦克风音量推送到 Linux 服务器上的「屏幕伴侣」插件。

与插件侧约定（无需随对话/配置项改动而改本脚本）:
  - 服务器上插件须将「截图来源」设为 remote，并启动中继服务（见插件 screen_relay_* 配置）。
  - 本程序填写的「端口」须与插件配置 screen_relay_port 一致（默认 8765）。
  - 二进制帧格式与 main._run_screen_relay_server 一致：无麦 4 字节大端 title 长度 + 标题 UTF-8 +
    4 字节大端 JPEG 长度 + 数据；有麦时帧头 \\xFF\\x01，帧尾 1 字节音量；\\xFE + 1 字节为仅音量更新。

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
    app.title("屏幕伴侣 · 推送")
    app.minsize(460, 520)
    app.resizable(True, True)

    # —— 现代简洁主题（ttk + clam，便于统一配色）——
    BG = "#eceef2"
    CARD = "#ffffff"
    TEXT = "#1a1c1e"
    MUTED = "#5c6370"
    BORDER = "#d1d5db"
    ACCENT = "#2563eb"
    ACCENT_HOVER = "#1d4ed8"

    app.configure(bg=BG)
    style = ttk.Style(app)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=TEXT)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 9))
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 17, "bold"))
    style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=("Microsoft YaHei UI", 9))
    style.configure("Hint.TLabel", background=CARD, foreground=MUTED, font=("Microsoft YaHei UI", 8))
    style.configure("Section.TLabel", background=CARD, foreground=MUTED, font=("Microsoft YaHei UI", 8))
    style.configure("Status.TLabel", background=BG, foreground=MUTED, font=("Microsoft YaHei UI", 9))
    style.configure("StatusActive.TLabel", background=BG, foreground=ACCENT, font=("Microsoft YaHei UI", 9, "bold"))
    style.configure("TLabelframe", background=CARD, relief="flat", borderwidth=0)
    style.configure("TLabelframe.Label", background=CARD, foreground=MUTED, font=("Microsoft YaHei UI", 9, "bold"))
    style.configure("TCheckbutton", background=CARD, foreground=TEXT, font=("Microsoft YaHei UI", 9))
    style.map("TCheckbutton", background=[("active", CARD)])
    style.configure("TEntry", fieldbackground="#ffffff", foreground=TEXT, insertcolor=TEXT)
    style.configure("TSeparator", background=BORDER)
    style.configure(
        "Primary.TButton",
        background=ACCENT,
        foreground="#ffffff",
        borderwidth=0,
        focuscolor="none",
        font=("Microsoft YaHei UI", 9, "bold"),
        padding=(18, 8),
    )
    style.map("Primary.TButton", background=[("active", ACCENT_HOVER), ("disabled", "#93c5fd")])
    style.configure(
        "Ghost.TButton",
        background=CARD,
        foreground=TEXT,
        borderwidth=1,
        focuscolor="none",
        font=("Microsoft YaHei UI", 9),
        padding=(16, 8),
    )
    style.map(
        "Ghost.TButton",
        background=[("active", "#f3f4f6"), ("disabled", "#f3f4f6")],
        foreground=[("disabled", MUTED)],
    )

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

    # 顶栏标题
    header = ttk.Frame(app, padding=(20, 18, 20, 6))
    header.pack(fill=tk.X)
    ttk.Label(header, text="屏幕伴侣", style="Title.TLabel").pack(anchor=tk.W)
    ttk.Label(
        header,
        text="将本机画面推送到 AstrBot 插件（截图来源需为 remote）",
        style="Subtitle.TLabel",
    ).pack(anchor=tk.W, pady=(4, 0))

    ttk.Separator(app, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=20, pady=(4, 0))

    body = ttk.Frame(app, padding=(20, 14, 20, 8))
    body.pack(fill=tk.BOTH, expand=True)

    def _card(parent, title: str):
        wrap = tk.Frame(parent, bg=BG, highlightbackground=BORDER, highlightthickness=1)
        wrap.pack(fill=tk.X, pady=(0, 12))
        inner = ttk.Frame(wrap, style="Card.TFrame", padding=(14, 12, 14, 14))
        inner.pack(fill=tk.BOTH, expand=True)
        ttk.Label(inner, text=title.upper(), style="Section.TLabel").pack(anchor=tk.W, pady=(0, 10))
        form = ttk.Frame(inner, style="Card.TFrame")
        form.pack(fill=tk.X)
        return form

    # 连接
    form_conn = _card(body, "连接")
    row = 0
    ttk.Label(form_conn, text="服务器地址", style="Hint.TLabel").grid(row=row, column=0, sticky=tk.W, pady=(0, 2))
    ttk.Label(form_conn, text="端口", style="Hint.TLabel").grid(row=row, column=1, sticky=tk.W, padx=(16, 0), pady=(0, 2))
    row += 1
    var_host = tk.StringVar(value="127.0.0.1")
    entry_host = ttk.Entry(form_conn, textvariable=var_host, width=22)
    entry_host.grid(row=row, column=0, sticky=tk.W)
    var_port = tk.StringVar(value="8765")
    entry_port = ttk.Entry(form_conn, textvariable=var_port, width=8)
    entry_port.grid(row=row, column=1, sticky=tk.W, padx=(16, 0))
    row += 1
    ttk.Label(
        form_conn,
        text="端口须与插件配置 screen_relay_port 一致（默认 8765）",
        style="Hint.TLabel",
    ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))
    row += 1

    # 画面
    form_cap = _card(body, "画面")
    row = 0
    ttk.Label(form_cap, text="截图间隔（秒）", style="Hint.TLabel").grid(row=row, column=0, sticky=tk.W, pady=(0, 2))
    ttk.Label(form_cap, text="JPEG 质量（1–100）", style="Hint.TLabel").grid(row=row, column=1, sticky=tk.W, padx=(16, 0), pady=(0, 2))
    row += 1
    var_interval = tk.StringVar(value="3")
    entry_interval = ttk.Entry(form_cap, textvariable=var_interval, width=8)
    entry_interval.grid(row=row, column=0, sticky=tk.W)
    var_quality = tk.StringVar(value="70")
    entry_quality = ttk.Entry(form_cap, textvariable=var_quality, width=8)
    entry_quality.grid(row=row, column=1, sticky=tk.W, padx=(16, 0))
    row += 1
    var_window_only = tk.BooleanVar(value=False)
    cb_window = ttk.Checkbutton(form_cap, text="仅截取当前活动窗口", variable=var_window_only)
    cb_window.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(12, 0))
    if not _has_pygetwindow:
        cb_window.config(state=tk.DISABLED)
        ttk.Label(form_cap, text="需要安装 pygetwindow", style="Hint.TLabel").grid(
            row=row + 1, column=0, columnspan=2, sticky=tk.W, pady=(4, 0)
        )

    # 音频
    form_mic = _card(body, "音频")
    mr = 0
    var_mic = tk.BooleanVar(value=False)
    cb_mic = ttk.Checkbutton(form_mic, text="推送麦克风实时音量（与截图同连接）", variable=var_mic)
    cb_mic.grid(row=mr, column=0, columnspan=2, sticky=tk.W)
    mr += 1
    if not _has_mic_libs:
        cb_mic.config(state=tk.DISABLED)
        ttk.Label(form_mic, text="需要安装 pyaudio、numpy", style="Hint.TLabel").grid(
            row=mr, column=0, columnspan=2, sticky=tk.W, pady=(4, 0)
        )
        mr += 1
    ttk.Label(form_mic, text="采样频率（次/秒）", style="Hint.TLabel").grid(
        row=mr, column=0, sticky=tk.W, pady=(10, 2)
    )
    mr += 1
    var_mic_rate = tk.StringVar(value="10")
    entry_mic_rate = ttk.Entry(form_mic, textvariable=var_mic_rate, width=8)
    entry_mic_rate.grid(row=mr, column=0, sticky=tk.W)

    # 操作栏
    frame_btn = ttk.Frame(app, padding=(20, 4, 20, 12))
    frame_btn.pack(fill=tk.X)
    var_status = tk.StringVar(value="未连接")

    def _refresh_status_style(*_):
        s = var_status.get()
        if "已连接" in s or "正在连接" in s:
            label_status.configure(style="StatusActive.TLabel")
        else:
            label_status.configure(style="Status.TLabel")

    label_status = ttk.Label(frame_btn, textvariable=var_status, style="Status.TLabel")
    label_status.pack(side=tk.LEFT, fill=tk.X, expand=True, anchor=tk.W)
    var_status.trace_add("write", _refresh_status_style)

    btn_inner = ttk.Frame(frame_btn)
    btn_inner.pack(side=tk.RIGHT)
    btn_stop = ttk.Button(btn_inner, text="停止", style="Ghost.TButton", state=tk.DISABLED)
    btn_stop.pack(side=tk.RIGHT, padx=(8, 0))
    btn_start = ttk.Button(btn_inner, text="开始推送", style="Primary.TButton")
    btn_start.pack(side=tk.RIGHT)

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
    log_wrap = tk.Frame(app, bg=BG, highlightbackground=BORDER, highlightthickness=1)
    log_wrap.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 16))
    log_head = ttk.Frame(log_wrap, style="Card.TFrame", padding=(14, 10, 14, 0))
    log_head.pack(fill=tk.X)
    ttk.Label(log_head, text="运行日志", style="Section.TLabel").pack(anchor=tk.W)
    log_inner = tk.Frame(log_wrap, bg=CARD)
    log_inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=(0, 1))
    log_text = scrolledtext.ScrolledText(
        log_inner,
        height=9,
        state=tk.DISABLED,
        wrap=tk.WORD,
        font=("Consolas", 10),
        bg="#f8f9fb",
        fg=TEXT,
        insertbackground=TEXT,
        relief=tk.FLAT,
        borderwidth=0,
        highlightthickness=0,
        padx=12,
        pady=10,
    )
    log_text.pack(fill=tk.BOTH, expand=True)
    log(
        "就绪。地址填运行 AstrBot 的机器 IP；端口与插件「screen_relay_port」一致（默认 8765）；"
        "插件需开启截图来源 remote。"
    )

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
