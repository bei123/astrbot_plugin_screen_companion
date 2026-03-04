# 远程截图模式（Linux 服务器 + Windows 推屏）

## 发布 Windows exe 到 GitHub Releases

仓库已配置 GitHub Actions：**当 `screen_relay_client_win.py` 有改动并推送到默认分支（main/master）时**，会自动在 Windows 上打包为单文件 exe 并更新 Releases 中的 **screen-companion-client/latest**。用户可在本仓库的 Releases 页下载 `ScreenCompanion-Windows.exe`。也可在 Actions 页选择 “Screen Companion Client (Windows exe)” 工作流，点击 “Run workflow” 手动触发一次打包。

当 AstrBot 运行在**无图形界面的 Linux 服务器**上时，无法在本机截屏。可使用「远程截图」模式：

- **Linux 服务器**：运行 AstrBot，本插件在配置中开启 `capture_source: remote` 并**暴露一个 TCP 端口**。
- **Windows 电脑**：运行常驻脚本 `screen_relay_client_win.py`，定时截屏并推送到上述端口。

插件收到的最新一帧会作为 `/kp` 或自动观察的截图来源。

## 插件配置（AstrBot 配置中）

在屏幕伴侣插件的配置里增加或修改：

```yaml
# 截图来源：local = 本机截屏（需图形界面），remote = 接收 Windows 端推送
capture_source: remote

# 本插件监听的端口，Windows 客户端需连接此端口
screen_relay_port: 8765

# 监听地址，默认 0.0.0.0 表示接受任意网卡上的连接（确保防火墙放行该端口）
screen_relay_bind: "0.0.0.0"
```

- 若 `capture_source` 为 `local`（默认），行为与原来一致，使用本机 pyautogui 截屏。
- 若为 `remote`，插件启动时会启动 TCP 服务，监听 `screen_relay_port`，并**不再依赖**本机的 pyautogui/图形环境。

## Windows 端常驻程序（图形界面）

在**你的 Windows 电脑**上：

1. 安装依赖：
   ```bash
   pip install pyautogui Pillow pygetwindow
   ```
2. 运行客户端（带图形界面）：
   ```bash
   python screen_relay_client_win.py
   ```
   或直接双击 `screen_relay_client_win.py`（若已关联 Python）。
3. 在窗口中填写：
   - **服务器地址**：Linux 服务器 IP（插件所在机器）
   - **端口**：与插件配置中的 `screen_relay_port` 一致（默认 8765）
   - **截图间隔**：每多少秒推一帧（如 3）
   - **截图质量**：1–100，可按需调整
4. 可选勾选「仅截取当前活动窗口」「启用麦克风推送」等，点击 **开始推送**。

保持窗口运行；连接成功后，Linux 上的插件即可用最新一帧进行窥屏/点评。

### 麦克风监听（远程）

若在插件中开启了「启用麦克风监听」，且希望用 **Windows 本机麦克风** 触发，在客户端窗口勾选「启用麦克风推送」即可。需先安装：

```bash
pip install pyaudio numpy
```

勾选后客户端会实时推送麦克风音量（可设置采样次数/秒），服务器用该值做阈值判断并发送「声音提醒」等消息。

## 协议说明

客户端与服务器之间为 TCP，每帧格式：

- 4 字节大端整数：标题长度 N
- N 字节：当前窗口标题 UTF-8
- 4 字节大端整数：JPEG 长度 M
- M 字节：JPEG 截图数据

可据此自行实现其他平台的推送端（如 macOS、树莓派等）。
