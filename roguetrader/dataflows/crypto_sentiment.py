"""加密货币情绪数据源模块

本模块整合了加密货币市场情绪数据:
- Alternative.me 恐惧与贪婪指数
- CoinGecko 热门搜索币种
- 社区/社交媒体情绪分析

提供聚合的加密货币情绪报告功能。
"""

from datetime import datetime
from functools import lru_cache

from .onchain_data import _safe_get, get_fear_greed_index, resolve_coin_id


@lru_cache(maxsize=8)
def get_crypto_fear_greed(days: int = 30) -> str:
    """获取并分析加密货币恐惧与贪婪指数

    在Alternative.me原始数据基础上添加趋势分析,
    计算当前值与历史均值的对比。
    """
    raw = get_fear_greed_index(days)

    header = f"# Crypto Fear & Greed Index — Trend Analysis\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    # Parse the raw CSV data for trend
    lines = raw.split("\n")
    data_lines = [l for l in lines if l and not l.startswith("#") and not l.startswith("date,") and not l.startswith("Current")]

    if len(data_lines) < 2:
        return header + raw + "\nInsufficient data for trend analysis"

    values = []
    for line in data_lines:
        parts = line.split(",")
        if len(parts) >= 2:
            try:
                values.append(int(parts[1]))
            except ValueError:
                continue

    if not values:
        return header + raw

    current = values[0]
    avg_7d = sum(values[:7]) / min(len(values), 7)
    avg_30d = sum(values) / len(values)

    trend_lines = [
        raw,
        "",
        "--- Trend Analysis ---",
        f"Current value: {current}",
        f"7-day average: {avg_7d:.1f}",
        f"30-day average: {avg_30d:.1f}",
        f"7d vs 30d delta: {avg_7d - avg_30d:+.1f}",
    ]

    if current <= 20:
        trend_lines.append("Zone: EXTREME FEAR — historically a buying opportunity")
    elif current <= 40:
        trend_lines.append("Zone: FEAR — market cautious, potential opportunity")
    elif current <= 60:
        trend_lines.append("Zone: NEUTRAL — no strong directional signal")
    elif current <= 80:
        trend_lines.append("Zone: GREED — market optimistic, exercise caution")
    else:
        trend_lines.append("Zone: EXTREME GREED — historically signals market tops")

    if avg_7d > avg_30d + 10:
        trend_lines.append("Trend: Sentiment improving rapidly (short-term)")
    elif avg_7d < avg_30d - 10:
        trend_lines.append("Trend: Sentiment deteriorating rapidly (short-term)")
    else:
        trend_lines.append("Trend: Sentiment relatively stable")

    return header + "\n".join(trend_lines)


@lru_cache(maxsize=4)
def get_crypto_trending() -> str:
    """从CoinGecko获取热门搜索币种数据"""
    data = _safe_get("https://api.coingecko.com/api/v3/search/trending")
    if not data:
        return "No trending coin data available"

    header = f"# Trending Cryptocurrencies (CoinGecko)\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    coins = data.get("coins", [])
    lines = ["rank,name,symbol,market_cap_rank,price_btc"]

    for i, coin_data in enumerate(coins, 1):
        item = coin_data.get("item", {})
        lines.append(
            f"{i},{item.get('name', 'N/A')},{item.get('symbol', 'N/A')},"
            f"#{item.get('market_cap_rank', 'N/A')},{item.get('price_btc', 'N/A'):.8f}"
        )

    # Also include trending categories if available
    categories = data.get("categories", [])
    if categories:
        lines.append("\n--- Trending Categories ---")
        for i, cat in enumerate(categories[:5], 1):
            lines.append(f"{i}. {cat.get('name', 'N/A')}")

    nfts = data.get("nfts", [])
    if nfts:
        lines.append("\n--- Trending NFTs ---")
        for i, nft in enumerate(nfts[:5], 1):
            lines.append(f"{i}. {nft.get('name', 'N/A')} (symbol: {nft.get('symbol', 'N/A')})")

    return header + "\n".join(lines)


@lru_cache(maxsize=16)
def get_crypto_social_sentiment(coin_id: str, curr_date: str) -> str:
    """从CoinGecko获取社交/社区情绪数据

    包含社区评分、开发者活动、社交媒体关注度等指标。
    """
    data = _safe_get(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}",
        params={
            "localization": "false",
            "tickers": "false",
            "market_data": "false",
            "community_data": "true",
            "developer_data": "true",
        },
    )
    if not data:
        return f"No social sentiment data available for {coin_id}"

    header = f"# Crypto Social Sentiment for {data.get('name', coin_id)}\n"
    header += f"# Reference date: {curr_date}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    community = data.get("community_data", {})
    developer = data.get("developer_data", {})
    scores = data.get("sentiment_votes_up_percentage", 0)
    scores_down = data.get("sentiment_votes_down_percentage", 0)

    lines = [
        "--- Community Metrics ---",
        "Twitter/X Followers: " + f"{community.get('twitter_followers', 'N/A'):,}" if isinstance(community.get('twitter_followers'), (int, float)) else f"{community.get('twitter_followers', 'N/A')}",
        "Reddit Subscribers: " + f"{community.get('reddit_subscribers', 'N/A'):,}" if isinstance(community.get('reddit_subscribers'), (int, float)) else f"{community.get('reddit_subscribers', 'N/A')}",
        "Reddit Active Accounts (48h): " + f"{community.get('reddit_accounts_active_48h', 'N/A'):,}" if isinstance(community.get('reddit_accounts_active_48h'), (int, float)) else f"{community.get('reddit_accounts_active_48h', 'N/A')}",
        f"Reddit Avg Posts (48h): {community.get('reddit_average_posts_48h', 'N/A')}",
        f"Reddit Avg Comments (48h): {community.get('reddit_average_comments_48h', 'N')}",
        "Telegram Members: " + f"{community.get('telegram_channel_user_count', 'N/A'):,}" if isinstance(community.get('telegram_channel_user_count'), (int, float)) else f"{community.get('telegram_channel_user_count', 'N/A')}",
        "",
        "--- Developer Activity ---",
        f"GitHub Forks: {developer.get('forks', 'N/A')}",
        f"GitHub Stars: {developer.get('stars', 'N/A')}",
        f"GitHub Subscribers: {developer.get('subscribers', 'N/A')}",
        f"Total Issues: {developer.get('total_issues', 'N/A')}",
        f"Closed Issues: {developer.get('closed_issues', 'N/A')}",
        f"Pull Requests Merged: {developer.get('pull_requests_merged', 'N/A')}",
        f"Pull Request Contributors: {developer.get('pull_request_contributors', 'N/A')}",
        f"Commit Count (4 weeks): {developer.get('commit_count_4_weeks', 'N/A')}",
        "",
        "--- CoinGecko Sentiment ---",
        f"Sentiment Up: {scores}%",
        f"Sentiment Down: {scores_down}%",
        f"CoinGecko Score: {data.get('coingecko_score', 'N/A')}",
        f"Community Score: {data.get('community_score', 'N/A')}",
        f"Developer Score: {data.get('developer_score', 'N/A')}",
        f"Liquidity Score: {data.get('liquidity_score', 'N/A')}",
        f"Public Interest Score: {data.get('public_interest_score', 'N/A')}",
    ]

    return header + "\n".join(lines)


def get_aggregated_crypto_sentiment(ticker: str, curr_date: str) -> str:
    """将所有加密货币情绪数据源聚合生成单一报告"""
    coin_id = resolve_coin_id(ticker)

    header = f"# Aggregated Crypto Sentiment Report for {ticker}\n"
    header += f"# Reference date: {curr_date}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    sections = []

    # Fear & Greed
    fg = get_crypto_fear_greed(30)
    sections.append("## 1. Fear & Greed Index\n" + fg)

    # Social sentiment
    social = get_crypto_social_sentiment(coin_id, curr_date)
    sections.append("\n## 2. Social & Community Metrics\n" + social)

    # Trending
    trending = get_crypto_trending()
    sections.append("\n## 3. Trending Coins\n" + trending)

    return header + "\n\n".join(sections)
