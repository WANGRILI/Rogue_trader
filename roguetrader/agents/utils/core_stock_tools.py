"""
核心股票数据工具模块

本模块提供股票市场数据的 LangChain 工具封装，用于获取股票的 OHLCV 价格数据。
通过 route_to_vendor 路由到 core_stock_apis 供应商获取数据。
"""

from langchain_core.tools import tool
from typing import Annotated
from roguetrader.dataflows.interface import route_to_vendor


@tool
def get_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve stock price data (OHLCV) for a given ticker symbol.
    Uses the configured core_stock_apis vendor.
    Args:
        symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted dataframe containing the stock price data for the specified ticker symbol in the specified date range.
    """
    return route_to_vendor("get_stock_data", symbol, start_date, end_date)
