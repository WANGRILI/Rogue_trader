"""
链上数据工具模块

本模块提供加密货币链上数据的 LangChain 工具封装，用于获取区块链链上指标数据。
包括市场数据、交易所流量、DeFi TVL、稳定币供应量、比特币挖矿统计等。
直接从链上数据源和 DeFiLlama 获取数据。
"""

from langchain_core.tools import tool
from typing import Annotated
from functools import lru_cache

from roguetrader.dataflows.onchain_data import (
    resolve_coin_id,
    get_coin_market_data,
    get_coin_market_chart,
    get_exchange_flows_summary,
    get_stablecoin_supply,
    get_btc_onchain_summary,
    get_chain_tvl,
    get_protocol_tvl,
    COIN_TO_CHAIN,
)


@tool
def get_onchain_metrics(
    ticker: Annotated[str, "Ticker symbol, e.g. BTC-USD, ETH-USD"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """Retrieve on-chain market metrics for a cryptocurrency.

    Includes market cap, volume, circulating/total/max supply,
    fully diluted valuation, ATH/ATL, and multi-timeframe price changes.
    """
    coin_id = resolve_coin_id(ticker)
    return get_coin_market_data(coin_id, curr_date)


@tool
def get_whale_activity(
    ticker: Annotated[str, "Ticker symbol, e.g. BTC-USD, ETH-USD"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """Analyze exchange volume distribution as a proxy for whale activity.

    Shows which exchanges hold the most volume, concentration metrics,
    and trading activity distribution.
    """
    coin_id = resolve_coin_id(ticker)
    return get_exchange_flows_summary(coin_id, curr_date)


@tool
def get_defi_tvl(
    chain_or_protocol: Annotated[str, "Blockchain name (e.g. Ethereum, Solana) or DeFi protocol name (e.g. aave, uniswap)"],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """Retrieve Total Value Locked (TVL) data from DeFiLlama.

    Can fetch TVL for either a blockchain (e.g. Ethereum, Solana)
    or a specific DeFi protocol (e.g. aave, uniswap).
    """
    chain_or_protocol_lower = chain_or_protocol.lower()

    chain_names = {v.lower(): v for v in COIN_TO_CHAIN.values()}
    if chain_or_protocol_lower in chain_names:
        return get_chain_tvl(chain_names[chain_or_protocol_lower])

    for coin_id, chain_name in COIN_TO_CHAIN.items():
        if chain_or_protocol_lower in (coin_id.lower(), chain_name.lower()):
            return get_chain_tvl(chain_name)

    return get_protocol_tvl(chain_or_protocol_lower)


@tool
def get_stablecoin_flows(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """Retrieve stablecoin supply and market cap data.

    Shows top stablecoins by circulating supply, indicating
    capital available for crypto markets.
    """
    return get_stablecoin_supply(curr_date)


@tool
def get_mining_stats(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
) -> str:
    """Retrieve Bitcoin mining and network statistics.

    Includes hash rate, difficulty, transaction count, active addresses,
    miner revenue, and transaction volume with 7-day trends.
    """
    return get_btc_onchain_summary(curr_date)
