# 我会一直看着你

astrbot_plugin_screen_companion 是面向 AstrBot 的屏幕伙伴插件。它能让bot随机地监视你的屏幕，并根据场景与用户进行互动。插件能监控音量、电脑状态以及你打开的窗口并进行合适的互动，且会根据每天的观察记录完成日记。此外，插件还拥有一个完善的webUI界面。灵感来源于妹居物语，适配live2D桌宠。

基本功能已经完善，现已转入维护阶段，如果喜欢的话请给个 star 吧。

## 版本

当前版本：`2.8.0`
`2.8.0` 版本已完成主文件拆分，并补上学习链路、模式感知、任务收尾感、学习回滚，以及日记提示词与排版优化等一整轮体验优化。当前重点已转向稳态维护和细节打磨。

## 主要功能

- 自动识屏：按间隔和概率观察当前屏幕，并在合适的时候主动回复。
- 即时识屏：`/kp` 固定截图识别，`/kpr` 固定录屏识别。
- 监控任务：监控音量/内存占用/电池电量，触发阈值可配置。
- 拟人化行为：根据用户互动和环境变化，调整回复内容和频率。
- 外部视觉 API：支持使用外部视觉模型进行屏幕识别。
- 录屏轻量采样：录屏模式会先抽取关键帧，必要时再回退到完整视频分析。
- 主动陪伴：支持变化感知、相似回复冷却、同窗口频率限制、手动发言后暂缓打断，以及敏感界面沉默、情绪短缓存和只观察不发言。
- 模式感知：看片时更偏陪伴，编程/办公时更偏助手，深度专注时进一步降低主动打断。
- 学习与纠偏：支持手动纠正、自然反馈学习、共同体验追问、误学回滚和学习开关矩阵。
- 任务收尾感：在工作场景明显告一段落时，更容易顺势补一句下一步引导。
- 长期记忆：保留窗口、场景、情节记忆和重复关注点，后续回复会优先召回相关记忆。
- 今日日记：自动生成更自然的日记正文，并同步生成结构化摘要与观察时间线。
- WebUI：查看运行状态、观察记录、活动统计、记忆，以及按“正文 - 概览 - 观察”拆开展示的日记信息。
- 插件api：提供插件之间的通信接口，支持自定义插件功能。

## 运行环境

远程识屏易产生隐私问题，仅推荐在本地部署的情况下使用，推荐在带图形桌面的环境中运行：

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

录屏模式必须安装 `ffmpeg`用于后台录制屏幕处理视频。如果没有 `ffmpeg`，插件仍可使用截图模式，但无法使用 `/kpr` 和录屏识屏。

### Windows 快速配置

0. 尝试直接从插件的release页面下载Windows版本的 `ffmpeg.exe`。
1. 或者从 [Gyan FFmpeg Builds](https://www.gyan.dev/ffmpeg/builds/) 下载 `ffmpeg-release-essentials.zip`。
2. 解压后找到 `ffmpeg.exe`，通常位于 `bin\ffmpeg.exe`。
3. 在 AstrBot 中执行：

```text
/kpi ffmpeg C:\你的路径\ffmpeg\bin\ffmpeg.exe
```

插件会自动把 `ffmpeg.exe` 复制到插件数据目录的 `bin` 文件夹。
Windows 默认路径通常是 `C:\Users\你的用户名\.astrbot\data\plugin_data\astrbot_plugin_screen_companion\bin\ffmpeg.exe`。
注：为避免插件更新导致需要重新安装 `ffmpeg`，现已于2.7.1版本已将 `ffmpeg.exe` 从插件本体文件夹移动到插件数据目录的 `bin` 文件夹中，原位置依旧兼容。


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
6. 用 `/kpi learning` 查看学习开关和最近学习动态。
7. 打开 WebUI 查看观察、日记、活动统计和记忆是否正常积累。

## 指令总览

相同功能的旧别名已经不再作为主要入口保留，下面只列推荐使用的简化版指令。

**注意**：为保证您不会被自己的bot开盒，所有指令仅管理员可使用：

### 即时识屏

- `/kp`：立即截图识别（仅管理员）。
- `/kpr`：立即录屏识别（仅管理员）。
- `/kps`：切换自动观察运行状态（仅管理员）。

### 自动观察与状态

- `/kpi start`：启动自动观察（仅管理员）。
- `/kpi stop`：停止自动观察（仅管理员）。
- `/kpi status`：查看自检、运行状态、主动目标、识屏链路和环境检查（仅管理员）。
- `/kpi help`：查看常用命令和最短上手路径（仅管理员）。
- `/kpi list`：查看当前任务列表（仅管理员）。
- `/kpi webui`：查看 WebUI 状态和访问地址（仅管理员）。
- `/kpi webui start`：启动 WebUI（仅管理员）。
- `/kpi webui stop`：停止 WebUI（仅管理员）。

### 预设与日记

- `/kpi p`：查看预设列表（仅管理员）。
- `/kpi ys [序号]`：使用指定预设；不带参数时显示预设列表（仅管理员）。
- `/kpi y [内容]`：记录一条观察（仅管理员）。
- `/kpi add [名称] [间隔秒] [概率]`：新增预设（仅管理员）。
- `/kpi d [日期]`：查看指定日期日记；凌晨两点前默认查看前一天（仅管理员）。
- `/kpi cd [日期]`：补写指定日期日记；凌晨两点前默认补写前一天（仅管理员）。

### 配置与调试

- `/kpi ffmpeg`：查看当前 `ffmpeg` 状态（仅管理员）。
- `/kpi ffmpeg [路径]`：设置 `ffmpeg` 路径并复制到插件数据目录（仅管理员）。
- `/kpi recent`：查看最近观察（仅管理员）。
- `/kpi correct [内容]`：补充纠正信息（仅管理员）。
- `/kpi preference [类别] [内容]`：记录偏好（仅管理员）。
- `/kpi learning`：查看或调整学习开关，并查看最近学习原因（仅管理员）。
- `/kpi learning [manual|feedback|followup|preference] [on|off]`：单独开关某类学习（仅管理员）。
- `/kpi learned`：查看最近自动学习记录（仅管理员）。
- `/kpi unlearn [序号|all]`：删除指定误学记录或清空自动学习记录（仅管理员）。
- `/kpi debug [on|off]`：切换调试模式（仅管理员）。

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
- `enable_manual_correction_learning`
- `enable_natural_feedback_learning`
- `enable_shared_activity_followup`
- `enable_shared_activity_preference_learning`

## 常见问题

### `/kpr` 提示找不到 `ffmpeg`

参考上述如何安装 ffmpeg。

### 识屏分析失败

请检查您的模型是否属于多模态模型且是否支持视频或多模态图片输入。注意，deepseek不是多模态模型，无法使用视觉分析功能。

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
- 确保 WebUI 访问密码安全，避免被他人获取。
- 不要在公共网络上开启 WebUI，避免被他人访问。
- 请合法使用，不要用于任何违法或不道德的目的。任何由于使用插件而导致的问题，插件作者不承担任何责任。

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
