"""
交易代理模块

本模块包含RogueTrader系统的各类代理组件:
- 分析师(Analysts): 基本面、市场、新闻、社交媒体、链上分析师
- 研究员(Researchers): 多头和空头研究员
- 风险辩手(Risk Debators): 激进派、保守派和中立派风险辩手
- 管理者(Managers): 研究和投资组合管理器
- 交易员(Trader): 主要交易决策代理
"""

from .utils.agent_utils import create_msg_delete
from .utils.agent_states import AgentState, InvestDebateState, RiskDebateState
from .utils.memory import FinancialSituationMemory

from .analysts.fundamentals_analyst import create_fundamentals_analyst
from .analysts.market_analyst import create_market_analyst
from .analysts.news_analyst import create_news_analyst
from .analysts.social_media_analyst import create_social_media_analyst
from .analysts.onchain_analyst import create_onchain_analyst

from .researchers.bear_researcher import create_bear_researcher
from .researchers.bull_researcher import create_bull_researcher

from .risk_mgmt.aggressive_debator import create_aggressive_debator
from .risk_mgmt.conservative_debator import create_conservative_debator
from .risk_mgmt.neutral_debator import create_neutral_debator

from .managers.research_manager import create_research_manager
from .managers.portfolio_manager import create_portfolio_manager

from .trader.trader import create_trader

__all__ = [
    "FinancialSituationMemory",
    "AgentState",
    "create_msg_delete",
    "InvestDebateState",
    "RiskDebateState",
    "create_bear_researcher",
    "create_bull_researcher",
    "create_research_manager",
    "create_fundamentals_analyst",
    "create_market_analyst",
    "create_neutral_debator",
    "create_news_analyst",
    "create_aggressive_debator",
    "create_portfolio_manager",
    "create_conservative_debator",
    "create_social_media_analyst",
    "create_onchain_analyst",
    "create_trader",
]
