"""
基本面分析模块

该模块负责创建基本面分析师代理,用于评估资产的内在价值。
分析师通过获取和分析财务报表数据来进行基本面分析,包括:
- 公司概况和关键财务指标
- 资产负债表、现金流量表、损益表等财务报表
- 商业模式和竞争护城河
- 盈利能力趋势、资产负债表健康度、现金流生成能力
- 估值指标(P/E、P/B、EV/EBITDA)与历史和行业平均对比
- 增长轨迹、风险因素(债务水平、集中度风险、监管敞口)
生成的结构化报告包含关键指标汇总表和投资决策建议。
"""
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from roguetrader.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_insider_transactions,
    get_language_instruction,
)
from roguetrader.dataflows.config import get_config


# 创建基本面分析师
# 
# 参数:
#     llm: 语言模型实例,用于生成分析报告
# 
# 返回:
#     fundamentals_analyst_node: 基本面分析师节点函数,接受状态字典并返回分析结果
def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
        ]

        system_message = (
            """# Fundamental Analysis

You are RogueTrader's Lead Fundamental Analyst. Analyze {ticker} using the available financial data to assess its intrinsic value and investment potential.

## Your Process
1. **Data Collection**:
   - `get_fundamentals` for general company profile and key financial metrics
   - Request specific statements (`get_balance_sheet`, `get_cashflow`, `get_income_statement`) for deeper dive

2. **Analysis Dimensions**:
   - **Business Model**: What's the company's core business and competitive moat?
   - **Financial Health**: Profitability trends, balance sheet strength, cash flow generation
   - **Valuation Metrics**: P/E, P/B, EV/EBITDA compared to historical and industry averages
   - **Growth Trajectory**: Revenue and earnings growth expectations
   - **Risk Factors**: Debt levels, concentration risks, regulatory exposure

3. **Output**: Deliver a structured fundamental analysis with a summary table of key metrics. Conclude with how this fundamental picture influences the investment decision.
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
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
