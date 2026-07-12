"""链上数据源模块 - 用于加密货币分析

本模块集成了多个链上数据API:
- CoinGecko: 市场价格、OHLC、K线、交易所流量数据
- DeFiLlama: DeFi协议TVL、链上TVL、稳定币供应量
- Blockchain.com: BTC链上统计数据（算力、难度、交易数等）
- Alternative.me: 加密货币恐惧贪婪指数

所有函数返回格式化的字符串,与现有yfinance工具保持一致。
"""

import time
from datetime import datetime, timedelta
from functools import lru_cache

import requests

# ---------------------------------------------------------------------------
# Ticker -> CoinGecko coin_id mapping
# ---------------------------------------------------------------------------

TICKER_TO_COINGECKO = {
    "BTC-USD": "bitcoin",
    "BTC-USDT": "bitcoin",
    "ETH-USD": "ethereum",
    "ETH-USDT": "ethereum",
    "SOL-USD": "solana",
    "SOL-USDT": "solana",
    "BNB-USD": "binancecoin",
    "BNB-USDT": "binancecoin",
    "XRP-USD": "ripple",
    "XRP-USDT": "ripple",
    "ADA-USD": "cardano",
    "ADA-USDT": "cardano",
    "DOGE-USD": "dogecoin",
    "DOGE-USDT": "dogecoin",
    "DOT-USD": "polkadot",
    "DOT-USDT": "polkadot",
    "AVAX-USD": "avalanche-2",
    "AVAX-USDT": "avalanche-2",
    "MATIC-USD": "matic-network",
    "MATIC-USDT": "matic-network",
    "LINK-USD": "chainlink",
    "LINK-USDT": "chainlink",
    "UNI-USD": "uniswap",
    "UNI-USDT": "uniswap",
    "ATOM-USD": "cosmos",
    "ATOM-USDT": "cosmos",
    "LTC-USD": "litecoin",
    "LTC-USDT": "litecoin",
    "FIL-USD": "filecoin",
    "FIL-USDT": "filecoin",
    "APT-USD": "aptos",
    "APT-USDT": "aptos",
    "ARB-USD": "arbitrum",
    "ARB-USDT": "arbitrum",
    "OP-USD": "optimism",
    "OP-USDT": "optimism",
    "SUI-USD": "sui",
    "SUI-USDT": "sui",
    "NEAR-USD": "near",
    "NEAR-USDT": "near",
}

# Blockchain name mapping for DeFiLlama
COIN_TO_CHAIN = {
    "bitcoin": "Bitcoin",
    "ethereum": "Ethereum",
    "solana": "Solana",
    "binancecoin": "BSC",
    "avalanche-2": "Avalanche",
    "matic-network": "Polygon",
    "arbitrum": "Arbitrum",
    "optimism": "Optimism",
    "near": "Near",
    "sui": "Sui",
    "cosmos": "Cosmos",
}

_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json", "User-Agent": "RogueTrader/1.0"})

REQUEST_TIMEOUT = 15


def resolve_coin_id(ticker: str) -> str:
    """将yfinance格式的ticker转换为CoinGecko的coin_id"""
    upper = ticker.strip().upper()
    if upper in TICKER_TO_COINGECKO:
        return TICKER_TO_COINGECKO[upper]

    # Fallback: try CoinGecko search API
    try:
        resp = _SESSION.get(
            "https://api.coingecko.com/api/v3/search",
            params={"query": upper.split("-")[0]},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            coins = resp.json().get("coins", [])
            if coins:
                return coins[0]["id"]
    except Exception:
        pass

    return upper.lower()


def _safe_get(url: str, params: dict = None) -> dict | list | None:
    """HTTP GET请求封装,包含错误处理和限流退避机制"""
    try:
        resp = _SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429:
            time.sleep(5)
            resp = _SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


# ===================================================================
# CoinGecko
# ===================================================================

@lru_cache(maxsize=32)
def get_coin_market_data(coin_id: str, curr_date: str) -> str:
    """从CoinGecko获取当前市场数据（市值、成交量、供应量等）"""
    data = _safe_get(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}",
        params={
            "localization": "false",
            "tickers": "false",
            "community_data": "false",
            "developer_data": "false",
        },
    )
    if not data:
        return f"No CoinGecko market data available for {coin_id}"

    md = data.get("market_data", {})
    lines = [
        f"# On-Chain Market Data for {data.get('name', coin_id).upper()} ({data.get('symbol', '').upper()})",
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Reference date: {curr_date}",
        "",
        f"Current Price (USD): ${md.get('current_price', {}).get('usd', 'N/A'):,}",
        f"Market Cap (USD): ${md.get('market_cap', {}).get('usd', 'N/A'):,}",
        f"Market Cap Rank: #{data.get('market_cap_rank', 'N/A')}",
        f"Fully Diluted Valuation (USD): ${md.get('fully_diluted_valuation', {}).get('usd', 'N/A'):,}",
        f"24h Trading Volume (USD): ${md.get('total_volume', {}).get('usd', 'N/A'):,}",
        f"Circulating Supply: {md.get('circulating_supply', 'N/A'):,}",
        f"Total Supply: {md.get('total_supply', 'N/A')}",
        f"Max Supply: {md.get('max_supply', 'N/A')}",
        "",
        f"ATH (USD): ${md.get('ath', {}).get('usd', 'N/A'):,}",
        f"ATH Date: {md.get('ath_date', {}).get('usd', 'N/A')}",
        f"ATH Change %: {md.get('ath_change_percentage', {}).get('usd', 'N/A')}%",
        f"ATL (USD): ${md.get('atl', {}).get('usd', 'N/A')}",
        f"ATL Date: {md.get('atl_date', {}).get('usd', 'N/A')}",
        "",
        f"24h Price Change %: {md.get('price_change_percentage_24h', 'N/A')}%",
        f"7d Price Change %: {md.get('price_change_percentage_7d', 'N/A')}%",
        f"14d Price Change %: {md.get('price_change_percentage_14d', 'N/A')}%",
        f"30d Price Change %: {md.get('price_change_percentage_30d', 'N/A')}%",
        f"60d Price Change %: {md.get('price_change_percentage_60d', 'N/A')}%",
        f"200d Price Change %: {md.get('price_change_percentage_200d', 'N/A')}%",
        f"1y Price Change %: {md.get('price_change_percentage_1y', 'N/A')}%",
    ]
    return "\n".join(lines)


@lru_cache(maxsize=32)
def get_coin_ohlc(coin_id: str, days: int = 30) -> str:
    """从CoinGecko获取OHLC K线数据,返回CSV格式字符串"""
    data = _safe_get(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc",
        params={"vs_currency": "usd", "days": str(days)},
    )
    if not data:
        return f"No OHLC data available for {coin_id}"

    header = f"# OHLC data for {coin_id} (last {days} days)\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    header += "timestamp,open,high,low,close\n"

    rows = []
    for candle in data:
        ts = datetime.fromtimestamp(candle[0] / 1000).strftime("%Y-%m-%d %H:%M")
        rows.append(f"{ts},{candle[1]},{candle[2]},{candle[3]},{candle[4]}")

    return header + "\n".join(rows)


@lru_cache(maxsize=32)
def get_coin_market_chart(coin_id: str, days: int = 30) -> str:
    """从CoinGecko获取历史价格/市值/成交量数据"""
    data = _safe_get(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
        params={"vs_currency": "usd", "days": str(days)},
    )
    if not data:
        return f"No market chart data available for {coin_id}"

    header = f"# Market chart for {coin_id} (last {days} days)\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    header += "date,price_usd,market_cap_usd,volume_usd\n"

    prices = data.get("prices", [])
    mcaps = data.get("market_caps", [])
    volumes = data.get("total_volumes", [])

    rows = []
    for i in range(len(prices)):
        ts = datetime.fromtimestamp(prices[i][0] / 1000).strftime("%Y-%m-%d")
        price = prices[i][1]
        mcap = mcaps[i][1] if i < len(mcaps) else ""
        vol = volumes[i][1] if i < len(volumes) else ""
        rows.append(f"{ts},{price:.2f},{mcap:.0f},{vol:.0f}")

    return header + "\n".join(rows)


# ===================================================================
# DeFiLlama
# ===================================================================

@lru_cache(maxsize=32)
def get_protocol_tvl(protocol: str) -> str:
    """从DeFiLlama获取DeFi协议的TVL历史数据"""
    data = _safe_get(f"https://api.llama.fi/protocol/{protocol}")
    if not data:
        return f"No TVL data available for protocol '{protocol}'"

    name = data.get("name", protocol)
    tvl_history = data.get("tvl", [])

    header = f"# TVL History for {name}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    recent = tvl_history[-30:] if len(tvl_history) > 30 else tvl_history
    lines = ["date,tvl_usd"]
    for entry in recent:
        ts = datetime.fromtimestamp(entry.get("date", 0)).strftime("%Y-%m-%d")
        lines.append(f"{ts},{entry.get('totalLiquidityUSD', 0):.0f}")

    return header + "\n".join(lines)


@lru_cache(maxsize=16)
def get_chain_tvl(chain: str) -> str:
    """从DeFiLlama获取区块链的TVL历史数据"""
    data = _safe_get(f"https://api.llama.fi/v2/historicalChainTvl/{chain}")
    if not data:
        return f"No TVL data available for chain '{chain}'"

    header = f"# Chain TVL History for {chain}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    recent = data[-30:] if len(data) > 30 else data
    lines = ["date,tvl_usd"]
    for entry in recent:
        ts = datetime.fromtimestamp(entry.get("date", 0)).strftime("%Y-%m-%d")
        lines.append(f"{ts},{entry.get('tvl', 0):.0f}")

    return header + "\n".join(lines)


@lru_cache(maxsize=8)
def get_stablecoin_supply(curr_date: str) -> str:
    """从DeFiLlama获取稳定币市场数据（供应量、市值）"""
    data = _safe_get("https://stablecoins.llama.fi/stablecoins?includePrices=true")
    if not data:
        return "No stablecoin supply data available"

    header = f"# Stablecoin Supply Overview\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    header += f"# Reference date: {curr_date}\n\n"

    stables = data.get("peggedAssets", [])
    lines = ["name,symbol,peg_type,circulating_usd"]

    top_stables = sorted(
        stables,
        key=lambda x: x.get("circulating", {}).get("peggedUSD", 0),
        reverse=True,
    )[:15]

    total = 0
    for s in top_stables:
        circ = s.get("circulating", {}).get("peggedUSD", 0)
        total += circ
        lines.append(
            f"{s.get('name', 'N/A')},{s.get('symbol', 'N/A')},"
            f"{s.get('pegType', 'N/A')},{circ:,.0f}"
        )

    lines.append(f"\nTotal Top-15 Stablecoin Market Cap: ${total:,.0f}")
    return header + "\n".join(lines)


# ===================================================================
# Blockchain.com (BTC-specific)
# ===================================================================

_BLOCKCHAIN_CHARTS = {
    "hash-rate": "Network Hash Rate (TH/s)",
    "difficulty": "Mining Difficulty",
    "n-transactions": "Daily Transaction Count",
    "mempool-size": "Mempool Size (bytes)",
    "miners-revenue": "Daily Miner Revenue (USD)",
    "transaction-fees-usd": "Daily Transaction Fees (USD)",
    "n-unique-addresses": "Daily Active Addresses",
    "estimated-transaction-volume-usd": "Estimated Transaction Volume (USD)",
}


@lru_cache(maxsize=16)
def get_btc_chain_stats(metric: str, timespan: str = "30days") -> str:
    """从Blockchain.com获取BTC链上指定指标数据"""
    if metric not in _BLOCKCHAIN_CHARTS:
        return f"Unknown metric '{metric}'. Available: {list(_BLOCKCHAIN_CHARTS.keys())}"

    data = _safe_get(
        f"https://api.blockchain.info/charts/{metric}",
        params={"timespan": timespan, "format": "json"},
    )
    if not data:
        return f"No data available for BTC metric '{metric}'"

    desc = _BLOCKCHAIN_CHARTS[metric]
    header = f"# BTC {desc}\n"
    header += f"# Timespan: {timespan}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    values = data.get("values", [])
    lines = ["date,value"]
    for v in values:
        ts = datetime.fromtimestamp(v["x"]).strftime("%Y-%m-%d")
        lines.append(f"{ts},{v['y']}")

    return header + "\n".join(lines)


def get_btc_onchain_summary(curr_date: str) -> str:
    """聚合多个BTC链上指标生成综合报告"""
    metrics = ["hash-rate", "difficulty", "n-transactions", "n-unique-addresses",
               "miners-revenue", "estimated-transaction-volume-usd"]
    sections = [
        f"# BTC On-Chain Summary",
        f"# Reference date: {curr_date}",
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    for m in metrics:
        data = _safe_get(
            f"https://api.blockchain.info/charts/{m}",
            params={"timespan": "7days", "format": "json"},
        )
        if data and data.get("values"):
            vals = data["values"]
            latest = vals[-1]["y"]
            prev = vals[0]["y"]
            change_pct = ((latest - prev) / prev * 100) if prev else 0
            sections.append(
                f"{_BLOCKCHAIN_CHARTS[m]}: {latest:,.2f} (7d change: {change_pct:+.1f}%)"
            )
        else:
            sections.append(f"{_BLOCKCHAIN_CHARTS[m]}: N/A")

    return "\n".join(sections)


# ===================================================================
# Alternative.me – Fear & Greed Index
# ===================================================================

@lru_cache(maxsize=8)
def get_fear_greed_index(days: int = 30) -> str:
    """从Alternative.me获取加密货币恐惧与贪婪指数历史数据"""
    data = _safe_get(
        "https://api.alternative.me/fng/",
        params={"limit": str(days), "format": "json"},
    )
    if not data or "data" not in data:
        return "No Fear & Greed Index data available"

    header = f"# Crypto Fear & Greed Index (last {days} days)\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    header += "# Scale: 0 = Extreme Fear, 100 = Extreme Greed\n\n"

    entries = data["data"]
    lines = ["date,value,classification"]
    for entry in entries:
        ts = datetime.fromtimestamp(int(entry["timestamp"])).strftime("%Y-%m-%d")
        lines.append(f"{ts},{entry['value']},{entry['value_classification']}")

    if entries:
        latest = entries[0]
        lines.append(f"\nCurrent: {latest['value']} ({latest['value_classification']})")

    return header + "\n".join(lines)


# ===================================================================
# Whale tracking (CoinGecko top holders approximation)
# ===================================================================

@lru_cache(maxsize=16)
def get_exchange_flows_summary(coin_id: str, curr_date: str) -> str:
    """通过CoinGecko数据近似分析交易所流量分布

    由于CoinGecko免费版不提供直接的巨鲸/交易所流量数据,
    使用交易所交易量分布作为替代指标进行分析。
    """
    data = _safe_get(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/tickers",
        params={"include_exchange_logo": "false", "depth": "true"},
    )
    if not data:
        return f"No exchange flow data available for {coin_id}"

    tickers = data.get("tickers", [])
    header = f"# Exchange Volume Distribution for {coin_id}\n"
    header += f"# Reference date: {curr_date}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    exchange_volumes = {}
    for t in tickers:
        exchange = t.get("market", {}).get("name", "Unknown")
        vol = t.get("converted_volume", {}).get("usd", 0)
        exchange_volumes[exchange] = exchange_volumes.get(exchange, 0) + vol

    sorted_exchanges = sorted(exchange_volumes.items(), key=lambda x: x[1], reverse=True)[:15]
    total_vol = sum(v for _, v in sorted_exchanges)

    lines = ["exchange,volume_usd,share_pct"]
    for name, vol in sorted_exchanges:
        pct = (vol / total_vol * 100) if total_vol else 0
        lines.append(f"{name},{vol:,.0f},{pct:.1f}%")

    lines.append(f"\nTotal tracked volume: ${total_vol:,.0f}")

    concentration = sum(v for _, v in sorted_exchanges[:3]) / total_vol * 100 if total_vol else 0
    lines.append(f"Top-3 exchange concentration: {concentration:.1f}%")

    return header + "\n".join(lines)
