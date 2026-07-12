"""
Stockstats数据处理工具模块

本模块提供与stockstats库配合使用的工具函数和类,包括:
- Yahoo Finance API调用的重试机制
- DataFrame数据清洗和规范化
- OHLCV数据加载和缓存
- 财务报表日期过滤
- 技术指标计算工具类

所有数据获取都经过处理以防止未来数据泄露(look-ahead bias)。
"""

import time
import logging

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError
from stockstats import wrap
from typing import Annotated
import os
from .config import get_config

logger = logging.getLogger(__name__)


def yf_retry(func, max_retries=3, base_delay=2.0):
    """执行yfinance API调用,在遇到速率限制时使用指数退避策略重试。

    yfinance在收到HTTP 429响应时会抛出YFRateLimitError,但不会自动重试。
    此包装器专门为速率限制添加重试逻辑,其他异常会立即传播。
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except YFRateLimitError:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Yahoo Finance rate limited, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                raise


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """规范化股票DataFrame以适配stockstats库:解析日期、删除无效行、填充价格缺口。"""
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])

    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()

    return data


def load_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """获取OHLCV数据并缓存,过滤掉curr_date之后的日期以防止前瞻偏差。

    下载5年数据至今天并按股票代码缓存。后续调用会重用缓存。
    curr_date之后的行被过滤掉,确保回测时不会看到未来价格。
    """
    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    # 缓存使用固定窗口(5年至今天),每个股票一个文件
    today_date = pd.Timestamp.today()
    start_date = today_date - pd.DateOffset(years=5)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today_date.strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = os.path.join(
        config["data_cache_dir"],
        f"{symbol}-YFin-data-{start_str}-{end_str}.csv",
    )

    if os.path.exists(data_file):
        data = pd.read_csv(data_file, on_bad_lines="skip")
    else:
        data = yf_retry(lambda: yf.download(
            symbol,
            start=start_str,
            end=end_str,
            multi_level_index=False,
            progress=False,
            auto_adjust=True,
        ))
        data = data.reset_index()
        data.to_csv(data_file, index=False)

    data = _clean_dataframe(data)

    # 过滤到curr_date以防止回测中的前瞻偏差
    data = data[data["Date"] <= curr_date_dt]

    return data


def filter_financials_by_date(data: pd.DataFrame, curr_date: str) -> pd.DataFrame:
    """删除curr_date之后的财务报表列(财政期间时间戳)。

    yfinance的财务报表使用财政期间结束日期作为列名。
    curr_date之后的列代表未来数据,会被移除以防止前瞻偏差。
    """
    if not curr_date or data.empty:
        return data
    cutoff = pd.Timestamp(curr_date)
    mask = pd.to_datetime(data.columns, errors="coerce") <= cutoff
    return data.loc[:, mask]


class StockstatsUtils:
    """提供基于stockstats库计算股票技术指标的静态方法工具类。"""

    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[
            str, "quantitative indicators based off of the stock data for the company"
        ],
        curr_date: Annotated[
            str, "curr date for retrieving stock price data, YYYY-mm-dd"
        ],
    ):
        """根据股票数据计算指定日期的技术指标值。"""
        data = load_ohlcv(symbol, curr_date)
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        curr_date_str = pd.to_datetime(curr_date).strftime("%Y-%m-%d")

        df[indicator]  # 触发stockstats计算指标
        matching_rows = df[df["Date"].str.startswith(curr_date_str)]

        if not matching_rows.empty:
            indicator_value = matching_rows[indicator].values[0]
            return indicator_value
        else:
            return "N/A: Not a trading day (weekend or holiday)"
