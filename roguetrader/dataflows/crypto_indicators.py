"""加密货币技术指标模块

本模块计算加密货币特有的技术分析指标:
- Pi Cycle Top/Bottom: 比特币周期顶部/底部指标
- NVT Ratio: 网络价值与交易比率（类似股票的市盈率）
- CME Gap: 芝加哥商品交易所期货缺口检测
- Funding Rate: 资金费率分析（从CoinGecko永续合约数据估算）

这些指标帮助识别市场周期位置和潜在反转点。
"""

from datetime import datetime, timedelta
from functools import lru_cache

import pandas as pd
import yfinance as yf

from .onchain_data import (
    get_coin_ohlc,
    get_btc_chain_stats,
    get_fear_greed_index as _raw_fear_greed,
    resolve_coin_id,
    _safe_get,
)


def _parse_coingecko_ohlc(csv_text: str) -> pd.DataFrame:
    """解析CoinGecko OHLC CSV文本为DataFrame"""
    lines = [l for l in csv_text.strip().split("\n") if not l.startswith("#") and l.strip()]
    if len(lines) < 2:
        return pd.DataFrame()

    from io import StringIO
    return pd.read_csv(StringIO("\n".join(lines)), parse_dates=["timestamp"])


def _parse_blockchain_csv(csv_text: str) -> pd.DataFrame:
    """解析Blockchain.com CSV文本为DataFrame"""
    lines = [l for l in csv_text.strip().split("\n") if not l.startswith("#") and l.strip()]
    if len(lines) < 2:
        return pd.DataFrame()

    from io import StringIO
    return pd.read_csv(StringIO("\n".join(lines)), parse_dates=["date"])


# ===================================================================
# Pi Cycle Top/Bottom Indicator
# ===================================================================

@lru_cache(maxsize=8)
def calculate_pi_cycle(symbol: str, curr_date: str) -> str:
    """计算Pi Cycle顶部/底部指标

    使用111日均线和350日均线*2的交叉来判断周期位置,
    当两条线交叉时,历史上往往标志着BTC主要周期顶部。
    需要约400天的数据才能得出有意义的结果。
    """
    coin_id = resolve_coin_id(symbol)

    # Fetch extended OHLC data (max free = 365 days for CoinGecko)
    ohlc_text = get_coin_ohlc(coin_id, days=365)
    df = _parse_coingecko_ohlc(ohlc_text)

    if df.empty or len(df) < 112:
        # Fallback to yfinance for longer history
        try:
            ticker = yf.Ticker(symbol.upper())
            hist = ticker.history(period="2y")
            if hist.empty:
                return f"Insufficient data for Pi Cycle calculation on {symbol}"
            df = hist.reset_index()
            df = df.rename(columns={"Date": "timestamp", "Close": "close"})
        except Exception:
            return f"Insufficient data for Pi Cycle calculation on {symbol}"

    if "close" not in df.columns:
        return "Pi Cycle: unable to parse price data"

    df = df.sort_values("timestamp").reset_index(drop=True)
    df["ma_111"] = df["close"].rolling(window=111, min_periods=111).mean()
    df["ma_350x2"] = df["close"].rolling(window=350, min_periods=111).mean() * 2

    latest = df.dropna(subset=["ma_111"]).iloc[-1] if not df.dropna(subset=["ma_111"]).empty else None
    if latest is None:
        return "Pi Cycle: insufficient data points for 111-day MA"

    header = f"# Pi Cycle Top/Bottom Indicator for {symbol}\n"
    header += f"# Reference date: {curr_date}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    price = latest["close"]
    ma111 = latest["ma_111"]
    ma350x2 = latest.get("ma_350x2")

    lines = [
        f"Current Price: ${price:,.2f}",
        f"111-day MA: ${ma111:,.2f}",
    ]

    if pd.notna(ma350x2):
        lines.append(f"350-day MA x2: ${ma350x2:,.2f}")
        gap_pct = (ma111 - ma350x2) / ma350x2 * 100
        lines.append(f"Gap between MAs: {gap_pct:+.2f}%")

        if ma111 > ma350x2:
            lines.append("Signal: 111 MA ABOVE 350x2 MA — potential cycle top zone (bearish)")
        elif gap_pct > -10:
            lines.append("Signal: MAs converging — approaching potential top (caution)")
        else:
            lines.append("Signal: 111 MA well below 350x2 MA — no top signal (neutral/bullish)")

        # Check for recent crossover
        recent = df.dropna(subset=["ma_111", "ma_350x2"]).tail(30)
        if len(recent) >= 2:
            prev_diff = recent.iloc[-2]["ma_111"] - recent.iloc[-2]["ma_350x2"]
            curr_diff = recent.iloc[-1]["ma_111"] - recent.iloc[-1]["ma_350x2"]
            if prev_diff <= 0 < curr_diff:
                lines.append("ALERT: Bullish-to-bearish crossover detected in last 30 days!")
            elif prev_diff >= 0 > curr_diff:
                lines.append("ALERT: Bearish-to-bullish crossover detected in last 30 days!")
    else:
        lines.append("350-day MA x2: N/A (need ~400 days of data)")
        lines.append("Signal: Insufficient history for full Pi Cycle analysis")

    return header + "\n".join(lines)


# ===================================================================
# NVT Ratio (Network Value to Transactions)
# ===================================================================

@lru_cache(maxsize=8)
def calculate_nvt_ratio(symbol: str, curr_date: str) -> str:
    """计算NVT比率 = 市值 / 链上交易量

    类似于股票的市盈率概念。高NVT表示估值偏高,
    低NVT表示估值偏低。目前仅支持BTC（使用Blockchain.com数据）。
    """
    coin_id = resolve_coin_id(symbol)

    header = f"# NVT Ratio Analysis for {symbol}\n"
    header += f"# Reference date: {curr_date}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    if coin_id != "bitcoin":
        return header + (
            f"NVT Ratio is currently only available for Bitcoin.\n"
            f"Requested: {symbol} ({coin_id})"
        )

    # Get market cap data from CoinGecko
    mcap_data = _safe_get(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
        params={"vs_currency": "usd", "days": "30"},
    )

    # Get on-chain transaction volume from Blockchain.com
    vol_text = get_btc_chain_stats("estimated-transaction-volume-usd", "30days")
    vol_df = _parse_blockchain_csv(vol_text)

    if mcap_data is None or vol_df.empty:
        return header + "Insufficient data to calculate NVT Ratio"

    mcaps = mcap_data.get("market_caps", [])
    if not mcaps:
        return header + "No market cap data from CoinGecko"

    # Latest values
    latest_mcap = mcaps[-1][1]
    latest_vol = vol_df.iloc[-1]["value"] if "value" in vol_df.columns else 0

    lines = []
    if latest_vol > 0:
        nvt = latest_mcap / (latest_vol * 365)
        lines.append(f"Market Cap: ${latest_mcap:,.0f}")
        lines.append(f"Daily On-chain Volume: ${latest_vol:,.0f}")
        lines.append(f"Annualized NVT Ratio: {nvt:.2f}")
        lines.append("")

        if nvt > 150:
            lines.append("Interpretation: VERY HIGH NVT — network potentially overvalued")
            lines.append("Historically signals reduced on-chain activity relative to valuation")
        elif nvt > 80:
            lines.append("Interpretation: HIGH NVT — caution, possible overvaluation")
        elif nvt > 40:
            lines.append("Interpretation: MODERATE NVT — fair value zone")
        elif nvt > 20:
            lines.append("Interpretation: LOW NVT — potentially undervalued")
        else:
            lines.append("Interpretation: VERY LOW NVT — strong on-chain activity vs valuation (bullish)")

        # 30-day trend
        if len(vol_df) >= 7 and len(mcaps) >= 7:
            nvt_7d_ago = mcaps[-7][1] / (vol_df.iloc[-7]["value"] * 365) if vol_df.iloc[-7]["value"] > 0 else 0
            if nvt_7d_ago > 0:
                nvt_change = (nvt - nvt_7d_ago) / nvt_7d_ago * 100
                lines.append(f"\n7-day NVT change: {nvt_change:+.1f}%")
                if nvt_change > 20:
                    lines.append("Trend: NVT rising sharply — on-chain usage declining relative to price")
                elif nvt_change < -20:
                    lines.append("Trend: NVT falling sharply — on-chain usage increasing (healthy)")
    else:
        lines.append("Unable to calculate NVT: on-chain volume data unavailable")

    return header + "\n".join(lines)


# ===================================================================
# CME Gap Detection
# ===================================================================

@lru_cache(maxsize=8)
def calculate_cme_gap(curr_date: str) -> str:
    """检测CME期货价格与BTC现货之间的缺口

    CME期货仅在周一至周五交易,周末会形成缺口。
    历史上CME缺口有约77%的回补率。
    """
    header = f"# BTC CME Gap Analysis\n"
    header += f"# Reference date: {curr_date}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    try:
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start = (curr_dt - timedelta(days=30)).strftime("%Y-%m-%d")

        # BTC spot (24/7)
        btc_spot = yf.Ticker("BTC-USD")
        spot_hist = btc_spot.history(start=start, end=curr_date)

        # CME BTC futures
        btc_cme = yf.Ticker("BTC=F")
        cme_hist = btc_cme.history(start=start, end=curr_date)

        if spot_hist.empty or cme_hist.empty:
            return header + "Insufficient data: could not fetch BTC spot or CME futures data"

    except Exception as e:
        return header + f"Error fetching CME data: {e}"

    lines = []

    # Find gaps: CME Friday close vs CME Monday open
    cme_hist = cme_hist.reset_index()
    if "Date" in cme_hist.columns:
        cme_hist["Date"] = pd.to_datetime(cme_hist["Date"])
        if cme_hist["Date"].dt.tz is not None:
            cme_hist["Date"] = cme_hist["Date"].dt.tz_localize(None)

    gaps = []
    for i in range(1, len(cme_hist)):
        prev_date = cme_hist.iloc[i - 1]["Date"]
        curr_date_row = cme_hist.iloc[i]["Date"]

        days_diff = (curr_date_row - prev_date).days
        if days_diff >= 2:
            friday_close = cme_hist.iloc[i - 1]["Close"]
            monday_open = cme_hist.iloc[i]["Open"]
            gap_pct = (monday_open - friday_close) / friday_close * 100
            gaps.append({
                "friday": prev_date.strftime("%Y-%m-%d"),
                "monday": curr_date_row.strftime("%Y-%m-%d"),
                "friday_close": friday_close,
                "monday_open": monday_open,
                "gap_pct": gap_pct,
                "filled": False,
            })

    # Check if gaps were filled
    for gap in gaps:
        monday_dt = datetime.strptime(gap["monday"], "%Y-%m-%d")
        future_data = cme_hist[cme_hist["Date"] >= monday_dt]
        if gap["gap_pct"] > 0:
            if any(future_data["Low"] <= gap["friday_close"]):
                gap["filled"] = True
        else:
            if any(future_data["High"] >= gap["friday_close"]):
                gap["filled"] = True

    if gaps:
        lines.append("Recent CME Gaps Detected:")
        lines.append("friday_close_date,monday_open_date,friday_close,monday_open,gap_pct,filled")
        for g in gaps[-5:]:
            lines.append(
                f"{g['friday']},{g['monday']},"
                f"${g['friday_close']:,.2f},${g['monday_open']:,.2f},"
                f"{g['gap_pct']:+.2f}%,{'Yes' if g['filled'] else 'No'}"
            )

        unfilled = [g for g in gaps if not g["filled"]]
        lines.append(f"\nTotal gaps in period: {len(gaps)}")
        lines.append(f"Unfilled gaps: {len(unfilled)}")

        if unfilled:
            latest_unfilled = unfilled[-1]
            lines.append(f"\nNearest unfilled gap: ${latest_unfilled['friday_close']:,.2f} "
                         f"(from {latest_unfilled['friday']})")
            lines.append("CME gaps have a ~77% historical fill rate — price tends to revisit these levels")
    else:
        lines.append("No CME gaps detected in the analysis period")

    # Current CME premium/discount
    if not spot_hist.empty and not cme_hist.empty:
        spot_latest = spot_hist["Close"].iloc[-1]
        cme_latest = cme_hist["Close"].iloc[-1]
        premium = (cme_latest - spot_latest) / spot_latest * 100
        lines.append(f"\nCurrent CME premium/discount: {premium:+.2f}%")
        lines.append(f"Spot: ${spot_latest:,.2f} | CME: ${cme_latest:,.2f}")

    return header + "\n".join(lines)


# ===================================================================
# Funding Rate (approximated from CoinGecko derivatives data)
# ===================================================================

@lru_cache(maxsize=16)
def get_funding_rate_data(symbol: str, curr_date: str) -> str:
    """从CoinGecko获取永续合约资金费率数据"""
    header = f"# Perpetual Funding Rate Analysis for {symbol}\n"
    header += f"# Reference date: {curr_date}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    coin_id = resolve_coin_id(symbol)

    # CoinGecko derivatives endpoint
    data = _safe_get(
        "https://api.coingecko.com/api/v3/derivatives",
        params={"include_tickers": "unexpired"},
    )
    if not data:
        return header + "No derivatives/funding rate data available"

    symbol_upper = symbol.upper().split("-")[0]
    relevant = [
        d for d in data
        if d.get("symbol", "").upper().startswith(symbol_upper)
        and "perpetual" in d.get("contract_type", "").lower()
    ]

    if not relevant:
        return header + f"No perpetual contracts found for {symbol}"

    lines = ["exchange,pair,funding_rate_pct,open_interest_usd,volume_24h_usd"]
    for d in relevant[:10]:
        fr = d.get("funding_rate", 0)
        oi = d.get("open_interest_usd", 0)
        vol = d.get("h24_volume", 0)
        lines.append(
            f"{d.get('market', 'N/A')},{d.get('symbol', 'N/A')},"
            f"{fr:.4f}%,{oi:,.0f},{vol:,.0f}"
        )

    # Aggregate analysis
    rates = [d.get("funding_rate", 0) for d in relevant if d.get("funding_rate")]
    if rates:
        avg_rate = sum(rates) / len(rates)
        lines.append(f"\nAverage Funding Rate: {avg_rate:.4f}%")
        if avg_rate > 0.05:
            lines.append("Interpretation: HIGH positive funding — longs paying shorts (crowded long, bearish signal)")
        elif avg_rate > 0.01:
            lines.append("Interpretation: Moderate positive funding — slightly long-biased market")
        elif avg_rate > -0.01:
            lines.append("Interpretation: Neutral funding — balanced market sentiment")
        elif avg_rate > -0.05:
            lines.append("Interpretation: Moderate negative funding — slightly short-biased (potential squeeze)")
        else:
            lines.append("Interpretation: HIGH negative funding — shorts paying longs (crowded short, bullish signal)")

    return header + "\n".join(lines)
