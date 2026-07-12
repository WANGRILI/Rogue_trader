"""
CLI数据模型定义

此模块定义了CLI中使用的所有数据模型和枚举类型。

主要类型:
- AnalystType: 分析师类型枚举
"""

from enum import Enum
from typing import List, Optional, Dict
from pydantic import BaseModel


class AnalystType(str, Enum):
    """
    分析师类型枚举

    用于标识和选择不同类型的金融分析师代理:
    - MARKET: 市场分析师 - 技术分析和价格走势
    - SOCIAL: 社交媒体分析师 - 社交媒体情绪分析
    - NEWS: 新闻分析师 - 新闻和事件影响分析
    - FUNDAMENTALS: 基本面分析师 - 财务和业务分析
    - ONCHAIN: 链上分析师 - 加密货币链上数据分析
    """
    MARKET = "market"
    SOCIAL = "social"
    NEWS = "news"
    FUNDAMENTALS = "fundamentals"
    ONCHAIN = "onchain"
