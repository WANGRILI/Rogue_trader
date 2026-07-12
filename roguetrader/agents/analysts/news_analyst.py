"""
宏观与加密货币新闻分析模块

该模块负责创建新闻与宏观分析师代理,用于分析特定资产的新闻舆情。
分析师会收集近期的宏观新闻、项目动态、监管公告等表面信息,同时深入分析
隐藏的权力博弈动态,包括监管机构的真实意图、政府持仓或禁令行为、国际关系
对采用的影响等。最终生成结构化的分析报告,包含关键事件、叙事分析和交易建议。
"""
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from roguetrader.agents.utils.agent_utils import (
    build_instrument_context,
    get_global_news,
    get_language_instruction,
    get_news,
)
from roguetrader.dataflows.config import get_config


# 创建新闻与宏观分析师
# 
# 参数:
#     llm: 语言模型实例,用于生成分析报告
# 
# 返回:
#     news_analyst_node: 新闻分析师节点函数,接受状态字典并返回分析结果
def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_news,
            get_global_news,
        ]

        system_message = (
            """# Macro & Crypto News Analysis - Including Hidden Power Dynamics

You are RogueTrader's Senior News & Macro Analyst. Your job is to synthesize the past week's news landscape AND analyze the **hidden power dynamics** that impact {ticker}.

## Your Approach
Analyze along multiple dimensions:

### 1. **Surface-Level Macro & Project News**
- **Macroeconomic Context**: Interest rate expectations, inflation data, central bank actions
- **Project/Company Specific**: Latest developments - technical upgrades, ecosystem growth, team changes
- **Market Sentiment**: General market mood from news coverage

### 2. **Regulatory & Geopolitical Hidden Agenda**
- What regulatory announcements are actually being made - what's not being said?
- Are governments secretly accumulating or banning this asset?
- How do international power rivalries affect adoption barriers?
- Is this asset being weaponized or sanctioned for geopolitical reasons?

### 3. **Influencer & Media Narrative Control**
- Who is pushing which narrative on social media?
- Are there coordinated shilling or FUD campaigns?
- What narrative are whales/market makers trying to push to manipulate retail?

### 4. **Trading Implications**
- What are concrete actionable conclusions accounting for both surface news AND hidden agendas?

Organize your findings into a structured report with clear section headings. End with a summary table highlighting key events, narratives, and their expected impact.
"""
            + get_language_instruction(),
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
            "news_report": report,
        }

    return news_analyst_node
