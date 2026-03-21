"""当前活动标识与工作/摸鱼分类，切换活动时写入 activity_history。"""

from __future__ import annotations

import time
from typing import Any


def update_activity(host: Any, scene: Any, active_window: Any) -> str:
    current_time = time.time()

    work_scenes = ["编程", "设计", "办公", "邮件", "浏览-工作"]
    play_scenes = ["游戏", "视频", "音乐", "社交", "浏览-娱乐"]

    activity_type = "其他"
    if scene in work_scenes:
        activity_type = "工作"
    elif scene in play_scenes:
        activity_type = "摸鱼"

    window_prefix = str(active_window or "")[:50]
    activity = f"{activity_type}:{scene}:{window_prefix}"

    if host.current_activity != activity:
        if host.current_activity and host.activity_start_time:
            host._append_activity_record(
                activity=host.current_activity,
                start_time=host.activity_start_time,
                end_time=current_time,
            )

        host.current_activity = activity
        host.activity_start_time = current_time

    return activity_type
