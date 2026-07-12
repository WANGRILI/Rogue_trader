"""
市场技术分析模块

该模块负责创建市场技术分析师代理,用于对股票或加密货币进行技术分析。
分析师会从可用指标中选择最相关的指标(如移动平均线、MACD、RSI、布林带等),
获取历史价格数据并计算技术指标,然后生成详细的技术分析报告,包括趋势识别、
支撑阻力位、动量背离、成交量分析等,为交易决策提供技术面支持。
"""
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from roguetrader.agents.utils.agent_utils import (
    build_instrument_context,
    get_indicators,
    get_language_instruction,
    get_stock_data,
)
from roguetrader.dataflows.config import get_config


# 创建市场技术分析师
# 
# 参数:
#     llm: 语言模型实例,用于生成分析报告
# 
# 返回:
#     market_analyst_node: 市场分析师节点函数,接受状态字典并返回分析结果
def create_market_analyst(llm):

    def market_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_stock_data,
            get_indicators,
        ]

        system_message = (
            """# Market Technical Analysis

You are RogueTrader's Chief Technical Analyst. Your responsibility is to conduct a comprehensive technical analysis of {ticker} based on current market conditions.

## Your Task
1. **Indicator Selection**: From the catalog below, select UP TO 6 indicators that are most relevant to the current market context. Each selected indicator should provide complementary, non-redundant information.
2. **Data Retrieval**: Always call `get_stock_data` first to get the price history CSV, then call `get_indicators` with your selected indicators.
3. **Analysis**: After receiving the calculated indicators, write a detailed technical analysis report that:
   - Identifies the current trend direction and strength
   - Highlights key support/resistance levels
   - Points out momentum divergences if any
   - Draws conclusions from volume/price interaction
   - Provides specific actionable trading implications

## Available Indicators

**Moving Averages:**
- `close_50_sma`: 50-day Simple Moving Average - medium trend identification
- `close_200_sma`: 200-day Simple Moving Average - major trend benchmark
- `close_10_ema`: 10-day Exponential Moving Average - short-term momentum capture

**Momentum oscillators:**
- `macd`/`macds`/`macdh`: MACD trio - trend momentum and divergence detection
- `rsi`: Relative Strength Index - overbought/oversold identification

**Volatility:**
- `boll`/`boll_ub`/`boll_lb`: Bollinger Bands - volatility and price compression/expansion
- `atr`: Average True Range - volatility measurement for stop-loss sizing

**Volume:**
- `vwma`: Volume Weighted Moving Average - trend confirmation by volume

**Selection Principles:**
- Prioritize indicators based on the visible price action
- Avoid redundant indicators (don't select both RSI and another momentum oscillator)
- Focus on quality over quantity - fewer deeper insights are better

Finally, structure your analysis clearly with a summary table of your findings and key price levels. Make your recommendations specific to help the trading team make decisions.
"""
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
            "market_report": report,
        }

    return market_analyst_node
