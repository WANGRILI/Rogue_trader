"""
加密货币情绪数据工具模块

本模块提供加密货币市场情绪分析的 LangChain 工具封装，用于获取情绪指标数据。
包括恐惧贪婪指数、社交媒体情绪、开发者活动、社区评分等综合情绪分析。
从多个数据源聚合情绪数据，帮助判断市场情绪周期。
"""

from langchain_core.tools import tool
from typing import Annotated

from roguetrader.dataflows.crypto_sentiment import (
    get_aggregated_crypto_sentiment,
    get_crypto_trending,
)


@tool
def get_crypto_sentiment(
    ticker: Annotated[str, "Ticker symbol, e.g. BTC-USD, ETH-USD"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """Retrieve aggregated crypto sentiment analysis.

    Combines Fear & Greed Index, social media metrics (Twitter/Reddit/Telegram),
    developer activity, CoinGecko community scores, and trending coins
    into a comprehensive sentiment report.
    """
    return get_aggregated_crypto_sentiment(ticker, curr_date)


@tool
def get_crypto_trending_coins() -> str:
    """Retrieve currently trending cryptocurrencies from CoinGecko.

    Shows the top trending coins, categories, and NFTs based on
    search volume and social activity.
    """
    return get_crypto_trending()
