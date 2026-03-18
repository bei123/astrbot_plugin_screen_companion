# 我会一直看着你

astrbot_plugin_screen_companion 是面向 AstrBot 的屏幕伙伴插件。它能让bot随机地监视你的屏幕，并根据场景与用户进行互动。插件能监控音量、电脑状态以及你打开的窗口并进行合适的互动，且会根据每天的观察记录完成日记。此外，插件还拥有一个完善的webUI界面。灵感来源于妹居物语，适配live2D桌宠。

基本功能已经完善，现已转入维护阶段，如果喜欢的话请给个 star 吧。

已知问题：在未手动结束自动化任务时重载插件，可能会导致原有的自动化任务持续进行且无法结束，如遇该情况请重启astrbot以免token消耗。

注：为避免插件更新导致需要重新安装 `ffmpeg`，现于2.7.1版本已将 `ffmpeg.exe` 从插件本体文件夹移动到插件数据目录的 `bin` 文件夹中，同时原位置依旧兼容。

## 版本

当前版本：`2.7.1`

## 主要功能

- 自动识屏：按间隔和概率观察当前屏幕，并在合适的时候主动回复。
- 即时识屏：`/kp` 固定截图识别，`/kpr` 固定录屏识别。
- 录屏轻量采样：录屏模式会先抽取关键帧，必要时再回退到完整视频分析。
- 主动陪伴：支持变化感知、相似回复冷却、同窗口频率限制和手动发言后暂缓打断。
- 长期记忆：保留窗口、场景、情节记忆和重复关注点，后续回复会优先召回相关记忆。
- 今日日记：自动生成自然语言日记，并同步生成结构化摘要。
- WebUI：查看运行状态、观察记录、活动统计、日记、记忆和可解释识屏信息。

## 运行环境

推荐在带图形桌面的环境中运行：

- Windows
- macOS
- Linux 图形桌面

额外要求：

- 截图模式需要系统截图权限。
- 录屏模式需要可用的 `ffmpeg`。
- 如果启用麦克风监听，需要系统麦克风权限。
- 如果启用外部视觉 API，需要正确配置模型地址、密钥和模型名。

## 安装

1. 将插件目录放入 AstrBot 插件目录，例如：

```text
C:\Users\你的用户名\.astrbot\data\plugins\astrbot_plugin_screen_companion
```

2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 重启 AstrBot。

注意：插件目录名必须是 `astrbot_plugin_screen_companion`，不要带版本号后缀。

## ffmpeg 安装

录屏模式必须安装 `ffmpeg`。如果没有 `ffmpeg`，插件仍可使用截图模式，但无法使用 `/kpr` 和录屏识屏。

### Windows 快速配置

0. 尝试从该插件的release中下载ffmpeg.exe。
1. 或者从 [Gyan FFmpeg Builds](https://www.gyan.dev/ffmpeg/builds/) 下载 `ffmpeg-release-essentials.zip`。
2. 解压后找到 `ffmpeg.exe`，通常位于 `bin\ffmpeg.exe`。
3. 在 AstrBot 中执行：

```text
/kpi ffmpeg C:\你的路径\ffmpeg\bin\ffmpeg.exe
```

插件会自动把 `ffmpeg.exe` 复制到插件数据目录的 `bin` 文件夹。
Windows 默认路径通常是 `C:\Users\你的用户名\.astrbot\data\plugin_data\astrbot_plugin_screen_companion\bin\ffmpeg.exe`。

### 手动配置

你也可以选择下面任意一种方式：

- 把 `ffmpeg.exe` 放到 `C:\Users\你的用户名\.astrbot\data\plugin_data\astrbot_plugin_screen_companion\bin\ffmpeg.exe`
- 在配置中填写完整的 `ffmpeg_path`
- 把 `ffmpeg` 加入系统 `PATH`

### macOS

```bash
brew install ffmpeg
```

### Linux

```bash
# Ubuntu / Debian
sudo apt install ffmpeg

# CentOS / RHEL
sudo yum install ffmpeg
```

## API 与识别链路

插件支持两种视觉识别方式。

### 外部视觉 API

适合你已经有单独视觉模型接口时使用。需要配置：

- `vision_api_url`
- `vision_api_key`
- `vision_api_model`

开启后，插件会优先把截图或采样后的录屏素材发送到外部视觉接口；当返回结果不稳定或信息不足时，会按当前策略决定是否继续走完整视频复判。

### 直接使用 AstrBot 当前 Provider 的多模态能力

当 `use_external_vision = false` 时，插件会把素材直接发送给 AstrBot 当前对话使用的多模态模型。

补充说明：

- 如果当前 Provider 是官方 Gemini API，图片会优先走 `inline_data`，视频会优先走 `Files API`。
- 如果 Provider 不适合直接吃完整视频，插件会优先使用轻量采样结果，减少超时与失败概率。
- 建议根据模型能力和网络情况，在外部视觉 API 与直连多模态之间选择更稳定的一条链路。

## 快速开始

1. 在配置里确认主动目标、识屏模式、模型和是否启用外部视觉 API。
2. 如需录屏模式，先执行 `/kpi ffmpeg` 或配置 `ffmpeg_path`。
3. 用 `/kp` 或 `/kpr` 做一次即时识屏，确认链路可用。
4. 用 `/kpi status` 查看当前运行状态与环境检查结果。
5. 用 `/kpi start` 启动自动观察。
6. 打开 WebUI 查看观察、日记、活动统计和记忆是否正常积累。

## 指令总览

相同功能的旧别名已经不再作为主要入口保留，下面只列推荐使用的简化版指令。

### 即时识屏

- `/kp`：立即截图识别。
- `/kpr`：立即录屏识别。
- `/kps`：保存当前截图。

### 自动观察与状态

- `/kpi start`：启动自动观察。
- `/kpi stop`：停止自动观察。
- `/kpi status`：查看自检、运行状态、主动目标、识屏链路和环境检查。
- `/kpi list`：查看当前任务列表。
- `/kpi webui`：查看 WebUI 状态和访问地址。
- `/kpi webui start`：启动 WebUI。
- `/kpi webui stop`：停止 WebUI。

### 预设与日记

- `/kpi p`：查看预设列表。
- `/kpi ys [序号]`：使用指定预设；不带参数时显示预设列表。
- `/kpi y [内容]`：记录一条观察。
- `/kpi add [名称] [间隔秒] [概率]`：新增预设。
- `/kpi d [日期]`：查看指定日期日记；凌晨两点前默认查看前一天。
- `/kpi cd [日期]`：补写指定日期日记；凌晨两点前默认补写前一天。

### 配置与调试

- `/kpi ffmpeg`：查看当前 `ffmpeg` 状态。
- `/kpi ffmpeg [路径]`：设置 `ffmpeg` 路径并复制到插件数据目录。
- `/kpi recent`：查看最近观察。
- `/kpi correct [内容]`：补充纠正信息。
- `/kpi preference [类别] [内容]`：记录偏好。
- `/kpi debug [on|off]`：切换调试模式。

## WebUI 能看什么

WebUI 当前适合做日常查看和排障：

- 运行状态：当前模式、任务、自检信息、最近主动消息和活动状态。
- 观察记录：识屏结果、触发原因、素材类型、识别摘要、最终回复。
- 今日日记：自然语言正文加结构化摘要。
- 活动统计：窗口活动时长、当前活动和持久化历史。
- 记忆：长期记忆、情节记忆、重复关注点。

默认地址通常是：

```text
http://127.0.0.1:1068
```

## 推荐关注的配置项

- `check_interval`
- `trigger_probability`
- `screen_recognition_mode`
- `ffmpeg_path`
- `recording_fps`
- `recording_duration_seconds`
- `use_external_vision`
- `vision_api_url`
- `vision_api_key`
- `vision_api_model`
- `allow_unsafe_video_direct_fallback`
- `webui_enabled`
- `webui_host`
- `webui_port`

## 常见问题

### `/kpr` 提示找不到 `ffmpeg`

先执行：

```text
/kpi ffmpeg C:\你的路径\ffmpeg\bin\ffmpeg.exe
```

然后重载插件或重启 AstrBot。也可以把 `ffmpeg.exe` 手动放到插件数据目录的 `bin` 文件夹。

### 关闭外部视觉 API 后仍然看到相关报错

请确认配置已保存并重载插件。新版本已经统一了布尔值读取，`"false"` 这类字符串不会再被误判为开启。

### 录屏模式容易超时

建议优先：

- 降低 `recording_duration_seconds`
- 降低 `recording_fps`
- 关闭不必要的外部视觉链路
- 使用支持视频或多模态图片输入的模型

当前版本已经加入轻量采样，会优先抽关键帧降低超时概率。

## 隐私与安全

- 截图、录屏、观察和记忆都可能包含你的屏幕内容，请只在信任的环境中使用。
- 配置外部视觉 API 时，请妥善保管密钥。
- 如果 WebUI 对外开放，请务必启用认证。

## 外部系统调用 API

允许外部系统通过 API 调用来分析图片。

### 启用方式

1. 开启 WebUI
2. 在 WebUI 设置中启用"允许外部 API 访问"
3. 配置 WebUI 访问密码（用于 API 认证）

### API 端点

#### 1. 文件上传方式

```
POST {webui_url}/api/analyze
```

参数（multipart/form-data）：
- `image`：图片文件（必填）
- `prompt`：自定义提示词（可选）
- `webhook`：回调地址，分析完成后异步推送结果（可选）

#### 2. Base64 方式

```
POST {webui_url}/api/analyze/base64
```

请求体（JSON）：
```json
{
  "image": "data:image/jpeg;base64,xxxxx",
  "prompt": "自定义提示词",
  "webhook": "https://your-callback-url.com"
}
```

### 认证方式

在请求头中添加 `X-API-Key`：
```
X-API-Key: 你的WebUI密码
```

### 示例

```bash
# 使用 curl 调用
curl -X POST http://localhost:6314/api/analyze/base64 \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_password" \
  -d '{"image": "data:image/jpeg;base64,..."}'

# 或者使用文件上传
curl -X POST http://localhost:6314/api/analyze \
  -H "X-API-Key: your_password" \
  -F "image=@screenshot.jpg"
```

### 注意事项

- 调用此 API 需要先配置 `vision_api_url`（外部视觉 API），因为图片分析依赖外部视觉服务
- 如果未设置 WebUI 密码，则无需认证（不推荐）
- deepseek不是多模态模型，无法使用视觉分析功能。

### 开发信息

- 开发者：menglimi（烛雨）
- qq：995051631    纯代码小白，出问题建议先问问豆包或deepseek，欢迎提交 issue 或 pull request，有好的建议或改进可以分享。
- 看到这里了，就祝您拉史顺畅，永不便秘，永不报错。
- 给个star吧，谢谢。
