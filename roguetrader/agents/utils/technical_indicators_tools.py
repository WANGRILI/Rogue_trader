"""
技术指标工具模块

本模块提供技术分析指标的 LangChain 工具封装，用于获取各种技术指标数据，
如 RSI、MACD 等。通过 route_to_vendor 路由到 technical_indicators 供应商获取数据。
支持逗号分隔的多个指标请求，会逐个处理并返回结果。
"""

from langchain_core.tools import tool
from typing import Annotated
from roguetrader.dataflows.interface import route_to_vendor


@tool
def get_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> str:
    """
    Retrieve a single technical indicator for a given ticker symbol.
    Uses the configured technical_indicators vendor.
    Args:
        symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
        indicator (str): A single technical indicator name, e.g. 'rsi', 'macd'. Call this tool once per indicator.
        curr_date (str): The current trading date you are trading on, YYYY-mm-dd
        look_back_days (int): How many days to look back, default is 30
    Returns:
        str: A formatted dataframe containing the technical indicators for the specified ticker symbol and indicator.
    """
    indicators = [i.strip() for i in indicator.split(",") if i.strip()]
    results = []
    for ind in indicators:
        try:
            results.append(route_to_vendor("get_indicators", symbol, ind, curr_date, look_back_days))
        except ValueError as e:
            results.append(str(e))
    return "\n\n".join(results)