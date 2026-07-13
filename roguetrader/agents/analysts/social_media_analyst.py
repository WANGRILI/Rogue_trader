"""
社交媒体与舆情分析模块

该模块负责创建社交媒体与舆情分析师代理,用于分析资产的社会舆情和叙事动态。
分析师不仅测量原始情绪,还深入分析谁在控制叙事舆论,包括恐惧与贪婪指数趋势、
KOL意见领袖的动机、社交媒体活动(特别是针对加密资产)、识别协调性拉盘/砸盘行为、
以及大户/做市商如何操纵散户舆情。报告将有机舆情与协调叙事分开,为交易决策提供情绪面参考。
"""
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from roguetrader.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_news,
    get_crypto_sentiment,
    render_agent_prompt,
)
from roguetrader.dataflows.config import get_config

CRYPTO_SUFFIXES = ("-USD", "-USDT", "-BTC", "-ETH", "-BUSD")


# 检查ticker是否为加密货币
# 
# 参数:
#     ticker: 交易对符号
# 
# 返回:
#     bool: 如果是加密货币则返回True,否则返回False
def _is_crypto_ticker(ticker: str) -> bool:
    upper = ticker.strip().upper()
    return any(upper.endswith(s) for s in CRYPTO_SUFFIXES)


# 创建社交媒体与舆情分析师
# 
# 参数:
#     llm: 语言模型实例,用于生成分析报告
# 
# 返回:
#     social_media_analyst_node: 社交媒体分析师节点函数,接受状态字典并返回分析结果
def create_social_media_analyst(llm, agent_registry=None):
    def social_media_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_news,
            get_crypto_sentiment,
        ]

        # Build system message dynamically based on whether it's crypto
        # Build system message with narrative warfare focus
        crypto_section = ""
        if _is_crypto_ticker(state["company_of_interest"]):
            crypto_section = """
### Crypto-Specific Narrative Analysis
- Track Fear & Greed Index trends and current level
- Monitor KOL opinion and social media buzz - who is paying whom
- Check activity on major crypto platforms (Reddit, Twitter, Chinese media)
- Identify coordinated bot activity vs organic discussion
"""

        system_message = (
            render_agent_prompt(
                agent_registry,
                "social_media_analyst",
                "You are RogueTrader's Social Media & Narrative Analyst.",
                "Analyze raw sentiment, narrative control, coordinated campaigns, and who benefits from the current narrative.",
                "Separate organic sentiment from coordinated narrative and explain trading implications.",
                f"""# Social Sentiment & Narrative Warfare Analysis

## Your Analysis
### 1. **Data Collection**:
- Use `get_news` to search recent social media and news mentions
- For crypto assets: use `get_crypto_sentiment` for aggregated sentiment metrics
{crypto_section}
### 2. **Sentiment Measurement**:
- What's the dominant sentiment - bullish, bearish, or neutral?
- How does current sentiment compare to historical levels?

### 3. **Narrative Control & Coordinated Campaigns**:
- Is there evidence of coordinated shilling or FUD?
- Which KOLs/influencers are pushing this narrative and what are their incentives?
- Are there astroturfing bot campaigns from competing projects or market makers?
- Is the narrative organic or being pushed by whales who want to manipulate retail?

### 4. **Trading Implications**:
- How does narrative manipulation risk affect entry/exit timing?
- Can we trade with the current narrative or should we fade it?

Deliver a comprehensive report with key insights summarized in a table at the end that separates organic sentiment from coordinated narrative.
""",
            )
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL DECISION: **BUY/OVERWEIGHT/HOLD/UNDERWEIGHT/SELL** or deliverable,"
                    " prefix your response with FINAL DECISION: **BUY/OVERWEIGHT/HOLD/UNDERWEIGHT/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "sentiment_report": report,
        }

    return social_media_analyst_node
