"""
CLI公告功能模块

此模块负责获取和显示来自远程端点的公告。
如果获取失败，会显示后备公告。

功能:
1. 从配置的URL获取公告
2. 显示公告面板
3. 可选的等待确认
"""

import getpass
import requests
from rich.console import Console
from rich.panel import Panel

from cli.config import CLI_CONFIG


def fetch_announcements(url: str = None, timeout: float = None) -> dict:
    """
    从端点获取公告

    从配置的URL获取JSON格式的公告。如果请求失败，
    返回后备公告。

    参数:
        url: 可选的公告URL（默认使用CLI_CONFIG中的URL）
        timeout: 请求超时时间（秒）

    返回:
        dict: {
            "announcements": List[str],  # 公告文本列表
            "require_attention": bool    # 是否需要用户确认
        }
    """
    endpoint = url or CLI_CONFIG["announcements_url"]
    timeout = timeout or CLI_CONFIG["announcements_timeout"]
    fallback = CLI_CONFIG["announcements_fallback"]

    try:
        response = requests.get(endpoint, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        return {
            "announcements": data.get("announcements", [fallback]),
            "require_attention": data.get("require_attention", False),
        }
    except Exception:
        return {
            "announcements": [fallback],
            "require_attention": False,
        }


def display_announcements(console: Console, data: dict) -> None:
    """
    在终端显示公告面板

    参数:
        console: Rich Console对象
        data: 包含公告数据的字典
    """
    announcements = data.get("announcements", [])
    require_attention = data.get("require_attention", False)

    if not announcements:
        return

    # 合并多个公告
    content = "\n".join(announcements)

    # 创建并显示面板
    panel = Panel(
        content,
        border_style="cyan",
        padding=(1, 2),
        title="Announcements",
    )
    console.print(panel)

    # 根据配置决定是否需要用户按Enter继续
    if require_attention:
        getpass.getpass("Press Enter to continue...")
    else:
        console.print()
