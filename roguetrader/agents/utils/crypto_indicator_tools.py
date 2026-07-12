"""
加密货币技术指标工具模块

本模块提供加密货币特有的技术分析指标 LangChain 工具封装，用于获取专业加密货币指标。
包括 Pi Cycle 顶底指标、NVT 比率、恐惧贪婪指数、资金费率、CME 期货缺口等。
这些指标专门为加密货币市场设计，帮助判断市场周期和趋势。
"""

from langchain_core.tools import tool
from typing import Annotated

from roguetrader.dataflows.crypto_indicators import (
    calculate_pi_cycle,
    calculate_nvt_ratio,
    calculate_cme_gap,
    get_funding_rate_data,
)
from roguetrader.dataflows.onchain_data import get_fear_greed_index


@tool
def get_pi_cycle_indicator(
    symbol: Annotated[str, "Ticker symbol, e.g. BTC-USD"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """Calculate the Pi Cycle Top/Bottom indicator.

    Uses the 111-day MA and 350-day MA * 2. When these cross,
    it historically marks major BTC cycle tops. Most useful for BTC
    but can be applied to other major cryptos.
    """
    return calculate_pi_cycle(symbol, curr_date)


@tool
def get_nvt_ratio(
    symbol: Annotated[str, "Ticker symbol, e.g. BTC-USD"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """Calculate the NVT Ratio (Network Value to Transactions).

    Similar to the PE ratio for stocks. NVT = Market Cap / On-chain Transaction Volume.
    High NVT suggests overvaluation, low NVT suggests undervaluation.
    Currently only available for Bitcoin.
    """
    return calculate_nvt_ratio(symbol, curr_date)


@tool
def get_crypto_fear_greed(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days of history to fetch"] = 30,
) -> str:
    """Retrieve the Crypto Fear & Greed Index.

    Scale: 0 = Extreme Fear, 100 = Extreme Greed.
    Historically, extreme fear has been a buying opportunity
    and extreme greed has signaled market tops.
    """
    return get_fear_greed_index(look_back_days)


@tool
def get_funding_rate(
    symbol: Annotated[str, "Ticker symbol, e.g. BTC-USD, ETH-USD"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """Retrieve perpetual futures funding rate data.

    Positive funding = longs paying shorts (crowded long, potentially bearish).
    Negative funding = shorts paying longs (crowded short, potential squeeze).
    """
    return get_funding_rate_data(symbol, curr_date)


@tool
def get_cme_gap(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """Detect BTC CME futures price gaps.

    CME futures trade Mon-Fri only; gaps form over weekends.
    Historically ~77% of CME gaps get filled.
    """
    return calculate_cme_gap(curr_date)
