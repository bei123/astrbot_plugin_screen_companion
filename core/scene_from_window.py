"""根据窗口标题推断粗略场景与浏览器页类型。"""

from __future__ import annotations


def classify_browser_content(window_title: str) -> str:
    """根据浏览器窗口标题分类内容类型。"""
    title_lower = window_title.lower()

    work_keywords = [
        "google", "baidu", "bing", "search", "查询", "搜索",
        "github", "gitlab", "coding", "stackoverflow", "stackexchange",
        "docs", "documentation", "wiki", "教程", "guide", "manual",
        "office", "excel", "word", "powerpoint", "spreadsheet", "document",
        "gmail", "outlook", "email", "mail", "邮件",
        "jira", "trello", "asana", "project", "task", "todo",
        "slack", "teams", "discord", "chat", "沟通", "协作",
        "figma", "design", "photoshop", "illustrator", "原型", "设计",
        "analytics", "data", "report", "dashboard", "分析", "报表",
        "code", "programming", "developer", "dev", "编程", "开发",
        "cloud", "aws", "azure", "gcp", "cloudflare", "服务器", "云",
        "crm", "erp", "sap", "salesforce", "客户", "管理",
        "learning", "course", "education", "学习", "课程", "教育",
    ]

    entertainment_keywords = [
        "youtube", "bilibili", "netflix", "hulu", "disney+", "视频", "电影", "剧集",
        "music", "spotify", "apple music", "网易云", "qq音乐", "音乐", "歌曲",
        "game", "gaming", "游戏", "steam", "epic", "游戏平台",
        "facebook", "instagram", "twitter", "x", "tiktok", "douyin", "社交", "微博",
        "news", "新闻", "头条", "资讯",
        "shopping", "电商", "淘宝", "京东", "拼多多", "购物", "商城",
        "sports", "体育", "足球", "篮球", "赛事",
        "entertainment", "娱乐", "明星", "综艺",
        "anime", "动画", "漫画", "番剧",
        "porn", "xxx", "色情", "成人",
    ]

    for keyword in work_keywords:
        if keyword in title_lower:
            return "浏览-工作"

    for keyword in entertainment_keywords:
        if keyword in title_lower:
            return "浏览-娱乐"

    return "浏览"


def identify_scene(window_title: str) -> str:
    """由当前窗口标题得到粗略场景标签。"""
    if not window_title:
        return "未知"

    title_lower = window_title.lower()

    keyword_groups = {
        "编程": [
            "code", "vscode", "visual studio", "intellij", "pycharm", "idea",
            "eclipse", "sublime", "atom", "notepad++", "vim", "emacs",
            "phpstorm", "webstorm", "goland", "rider", "android studio", "xcode",
            "terminal", "powershell", "cmd", "git", "github", "gitlab", "coding",
            "dev", "developer", "program", "programming", "debug", "compile", "build",
            "python", "java", "c++", "c#", "javascript", "typescript", "html", "css",
            "ide", "editor", "console", "shell", "bash", "zsh", "powershell",
        ],
        "设计": [
            "photoshop", "illustrator", "figma", "sketch", "xd", "gimp", "canva",
            "photopea", "coreldraw", "blender", "maya", "3d", "design",
            "creative", "art", "graphic", "ui", "ux", "wireframe", "prototype",
            "adobe", "affinity", "paint", "draw", "illustration", "animation",
        ],
        "浏览": [
            "chrome", "firefox", "edge", "safari", "opera", "browser", "???",
            "chrome.exe", "firefox.exe", "edge.exe", "safari.exe", "opera.exe",
            "browser", "web", "internet", "chrome", "firefox", "edge", "safari", "opera",
        ],
        "办公": [
            "word", "excel", "powerpoint", "office", "??", "??", "wps", "outlook",
            "office365", "onenote", "access", "project", "visio",
            "document", "spreadsheet", "presentation", "calendar", "task", "todo",
            "work", "office", "business", "report", "data", "analysis", "excel",
        ],
        "游戏": [
            "steam", "epic", "battle.net", "valorant", "csgo", "dota", "minecraft",
            "game", "league", "lol", "overwatch", "fortnite", "pubg", "apex",
            "genshin", "roblox", "warcraft", "diablo", "starcraft", "hearthstone",
            "fifa", "nba", "call of duty", "cod", "assassin's creed", "ac",
            "grand theft auto", "gta", "the witcher", "cyberpunk", "fallout",
            "game", "gaming", "play", "player", "level", "mission", "quest",
            "character", "weapon", "map", "server", "multiplayer", "singleplayer",
        ],
        "视频": [
            "youtube", "bilibili", "netflix", "vlc", "potplayer", "movie", "video", "??",
            "youku", "tudou", "iqiyi", "letv", "mkv", "mp4", "wmv", "avi",
            "media player", "kmplayer", "mplayer",
            "video", "movie", "film", "tv", "show", "series", "episode", "streaming",
            "watch", "player", "media", "video", "movie", "film", "tv", "show",
        ],
        "阅读": [
            "novel", "reader", "ebook", "pdf", "reading", "??", "???", "???",
            "adobe reader", "foxit", "kindle", "ibooks", "epub", "mobi",
            "book", "read", "reading", "novel", "story", "document", "pdf", "epub",
        ],
        "音乐": [
            "spotify", "apple music", "music", "itunes", "?????", "qq??", "musicbee",
            "网易云", "netease", "kuwo", "kugou", "qq music", "winamp", "foobar",
            "music", "song", "audio", "player", "music", "song", "audio", "playlist",
        ],
        "社交": [
            "discord", "wechat", "qq", "skype", "zoom", "teams", "slack",
            "whatsapp", "telegram", "signal", "messenger", "facebook", "instagram",
            "twitter", "x", "linkedin", "tiktok", "douyin",
            "chat", "message", "social", "contact", "friend", "conversation",
        ],
        "邮件": [
            "outlook", "gmail", "mail", "thunderbird", "mailchimp", "protonmail",
            "邮件", "email", "inbox", "mail", "email", "message", "inbox", "outbox",
        ],
        "工具": [
            "calculator", "notepad", "paint", "snip", "snipping", "screenshot",
            "explorer", "finder", "file explorer", "task manager", "control panel",
            "tool", "utility", "app", "application", "program", "software",
        ],
    }

    for scene, keywords in keyword_groups.items():
        if any(keyword in title_lower for keyword in keywords):
            if scene == "浏览":
                return classify_browser_content(window_title)
            return scene

    loose_match = {
        "编程": ["代码", "程序", "开发", "debug", "编译", "运行"],
        "设计": ["设计", "创意", "美术", "绘图", "编辑"],
        "办公": ["文档", "表格", "演示", "会议", "工作"],
        "游戏": ["游戏", "游玩", "关卡", "任务", "角色"],
        "视频": ["视频", "电影", "电视", "节目", "播放"],
        "阅读": ["阅读", "书籍", "小说", "文档", "文章"],
        "音乐": ["音乐", "歌曲", "音频", "播放"],
        "社交": ["聊天", "消息", "社交", "联系", "朋友"],
        "邮件": ["邮件", "邮箱", "邮件", "发送", "接收"],
    }

    for scene, keywords in loose_match.items():
        if any(keyword in title_lower for keyword in keywords):
            return scene

    if len(title_lower) > 10:
        if any(browser in title_lower for browser in ["chrome", "firefox", "edge", "safari", "opera"]):
            return "浏览"
        if any(video in title_lower for video in ["youtube", "bilibili", "netflix", "video", "movie"]):
            return "视频"
        if any(game in title_lower for game in ["game", "steam", "epic"]):
            return "游戏"

    return "未知"
