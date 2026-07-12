"""
新闻数据工具模块

本模块提供新闻和内幕交易数据的 LangChain 工具封装，用于获取股票新闻资讯。
通过 route_to_vendor 路由到 news_data 供应商获取数据。使用 LRU 缓存减少重复请求。
"""

from langchain_core.tools import tool
from typing import Annotated
from functools import lru_cache
from roguetrader.dataflows.interface import route_to_vendor


@lru_cache(maxsize=64)
def _cached_get_news(ticker: str, start_date: str, end_date: str) -> str:
    """新闻数据缓存辅助函数，用于减少重复 API 调用"""
    return route_to_vendor("get_news", ticker, start_date, end_date)


@lru_cache(maxsize=16)
def _cached_get_global_news(curr_date: str, look_back_days: int, limit: int) -> str:
    """全球新闻缓存辅助函数，用于减少重复 API 调用"""
    return route_to_vendor("get_global_news", curr_date, look_back_days, limit)


@tool
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve news data for a given ticker symbol.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news data
    """
    return _cached_get_news(ticker, start_date, end_date)


@tool
def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days to look back"] = 7,
    limit: Annotated[int, "Maximum number of articles to return"] = 5,
) -> str:
    """
    Retrieve global news data.
    Uses the configured news_data vendor.
    Args:
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Number of days to look back (default 7)
        limit (int): Maximum number of articles to return (default 5)
    Returns:
        str: A formatted string containing global news data
    """
    return _cached_get_global_news(curr_date, look_back_days, limit)


@tool
def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """
    Retrieve insider transaction information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
    Returns:
        str: A report of insider transaction data
    """
    return route_to_vendor("get_insider_transactions", ticker)
