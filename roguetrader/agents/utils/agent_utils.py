"""
代理工具模块 - 为代理提供通用工具函数和提示构建

本模块提供:
- 语言指令获取: 根据配置返回多语言输出提示
- 交易标的上下文构建: 确保代理使用正确的交易所限定代码
- 消息删除功能: 用于状态清理和Anthropic兼容性
"""

from langchain_core.messages import HumanMessage, RemoveMessage

# 从各工具模块导入所需工具
from roguetrader.agents.utils.core_stock_tools import (
    get_stock_data
)
from roguetrader.agents.utils.technical_indicators_tools import (
    get_indicators
)
from roguetrader.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from roguetrader.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news
)
from roguetrader.agents.utils.onchain_data_tools import (
    get_onchain_metrics,
    get_whale_activity,
    get_defi_tvl,
    get_stablecoin_flows,
    get_mining_stats,
)
from roguetrader.agents.utils.local_crypto_data_tools import (
    get_local_crypto_ohlcv,
)
from roguetrader.agents.utils.crypto_indicator_tools import (
    get_pi_cycle_indicator,
    get_nvt_ratio,
    get_crypto_fear_greed,
    get_funding_rate,
    get_cme_gap,
)
from roguetrader.agents.utils.crypto_sentiment_tools import (
    get_crypto_sentiment,
    get_crypto_trending_coins,
)


# 获取语言指令函数 - 根据配置返回多语言输出提示
def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Only applied to user-facing agents (analysts, portfolio manager).
    Internal debate agents stay in English for reasoning quality.
    """
    from roguetrader.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}."


# 构建交易标的上下文函数 - 确保代理保留交易所限定的完整代码
def build_instrument_context(ticker: str) -> str:
    """Describe the exact instrument so agents preserve exchange-qualified tickers."""
    return (
        f"The instrument to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`)."
    )


def render_agent_prompt(
    agent_registry,
    agent_id: str,
    default_identity: str,
    default_focus: str,
    default_style: str,
    body: str,
) -> str:
    """Render a prompt with optional YAML-configured agent profile overrides."""
    if agent_registry is None:
        return (
            f"# Agent Identity\n{default_identity.strip()}\n\n"
            f"# Focus\n{default_focus.strip()}\n\n"
            f"# Response Style\n{default_style.strip()}\n\n"
            f"{body.strip()}"
        )
    return agent_registry.render_prompt(
        agent_id=agent_id,
        default_identity=default_identity,
        default_focus=default_focus,
        default_style=default_style,
        body=body,
    )


# 创建消息删除函数工厂 - 返回一个清除消息并添加占位符的函数
def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]

        # 移除所有现有消息
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        # 添加一个最小化的占位符消息以保持兼容性
        placeholder = HumanMessage(content="Continue")

        return {"messages": removal_operations + [placeholder]}

    return delete_messages
