"""
CLI配置文件

此模块包含CLI相关的配置常量。

配置项:
- announcements_url: 公告API端点URL
- announcements_timeout: 公告请求超时时间（秒）
- announcements_fallback: 请求失败时的后备公告文本
"""

CLI_CONFIG = {
    # 公告相关配置
    "announcements_url": "",  # 公告API端点，留空使用后备
    "announcements_timeout": 1.0,  # 请求超时时间（秒）
    "announcements_fallback": "[cyan]For more information, please visit[/cyan] [link=https://github.com/RogueTrader]https://github.com/RogueTrader[/link]",
}
