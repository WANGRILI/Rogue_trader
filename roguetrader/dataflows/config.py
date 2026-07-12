"""
配置管理模块

本模块负责管理应用程序的全局配置,提供配置的初始化、获取和更新功能。
使用默认配置,同时也支持运行时自定义配置覆盖。
"""

import roguetrader.default_config as default_config
from typing import Dict, Optional

# 全局配置变量,初始值为None,在首次访问时初始化
_config: Optional[Dict] = None


def initialize_config():
    """Initialize the configuration with default values."""
    global _config
    if _config is None:
        _config = default_config.DEFAULT_CONFIG.copy()


def set_config(config: Dict):
    """使用自定义值更新全局配置。与默认配置合并后生效。"""
    global _config
    if _config is None:
        _config = default_config.DEFAULT_CONFIG.copy()
    _config.update(config)


def get_config() -> Dict:
    """获取当前配置副本。如果配置尚未初始化,则先进行初始化。"""
    if _config is None:
        initialize_config()
    return _config.copy()


# 模块加载时自动使用默认配置进行初始化
initialize_config()
