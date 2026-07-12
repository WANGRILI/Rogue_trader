"""LangChain tools for local processed crypto data."""

from typing import Annotated

from langchain_core.tools import tool

from roguetrader.dataflows.local_crypto_data import get_local_ohlcv_report


@tool
def get_local_crypto_ohlcv(
    ticker: Annotated[str, "Ticker symbol, e.g. BTC-USD, BTC-USDT"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    days: Annotated[int, "Lookback window in days"] = 365,
    timeframe: Annotated[str, "Timeframe, e.g. 1d, 1h, 1m"] = "1d",
    source: Annotated[str, "Processed data source, e.g. manual_or_investing, okx"] = "manual_or_investing",
) -> str:
    """Retrieve local OHLCV summary from processed Parquet data.

    This tool follows the local data policy: raw2 is the upstream source of
    truth, but RogueTrader runtime reads only the standardized
    processed/parquet layer.
    """
    return get_local_ohlcv_report(
        ticker=ticker,
        curr_date=curr_date,
        days=days,
        timeframe=timeframe,
        source=source,
    )
