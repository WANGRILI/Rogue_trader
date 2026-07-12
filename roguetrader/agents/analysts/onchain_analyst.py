"""
链上数据分析模块

该模块负责创建链上数据分析师代理,专门用于分析加密货币资产的链上指标。
分析师会深入分析链上数据以理解驱动价格的权力结构和博弈动态,包括:
- 基本链上指标:市值、NVT比率、Token供应动态、交易所流入/流出
- 巨鲸与做市商控制分析:大户持币集中度、累积vs分配模式、交易所储备变化
- 监管与地缘政治分析:主要司法管辖区的监管态度、国家级持仓、货币政策影响
- Layer权力博弈:Layer1链 dominance争夺、Layer2的MEV和流动性捕获
- 技术指标:Pi Cycle、融资费率、CME期货缺口、恐惧与贪婪指数
最终生成包含权力结构评估、宏观博弈影响、最终交易决策的综合分析报告。
"""
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from roguetrader.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_onchain_metrics,
    get_whale_activity,
    get_defi_tvl,
    get_stablecoin_flows,
    get_mining_stats,
    get_pi_cycle_indicator,
    get_nvt_ratio,
    get_crypto_fear_greed,
    get_funding_rate,
    get_cme_gap,
    get_local_crypto_ohlcv,
)


# 创建链上数据分析师
# 
# 参数:
#     llm: 语言模型实例,用于生成分析报告
# 
# 返回:
#     onchain_analyst_node: 链上分析师节点函数,接受状态字典并返回分析结果
def create_onchain_analyst(llm):
    def onchain_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_local_crypto_ohlcv,
            get_onchain_metrics,
            get_whale_activity,
            get_defi_tvl,
            get_stablecoin_flows,
            get_mining_stats,
            get_pi_cycle_indicator,
            get_nvt_ratio,
            get_crypto_fear_greed,
            get_funding_rate,
            get_cme_gap,
        ]

        system_message = (
            """# On-Chain Crypto & Macro Power Structure Analysis

You are RogueTrader's Lead On-Chain Analyst specializing in crypto assets. Go beyond surface metrics - analyze not just price, but **the power structure and博弈 dynamics** that drive price.

## IMPORTANT INSTRUCTIONS:
1. **Use tools strategically**: Call 2-4 key tools to gather essential data, then STOP and analyze.
2. **Prefer local data first when available**: For price/OHLCV context, call `get_local_crypto_ohlcv` before online APIs. This tool reads standardized processed Parquet, not raw2 directly.
3. **DO NOT call all tools**: You don't need to call every single tool. Focus on the most relevant ones.
4. **After gathering data, provide final analysis**: Once you have enough data (2-4 tool calls), write your comprehensive analysis with a FINAL DECISION.
5. **MUST include FINAL DECISION**: End your analysis with "FINAL DECISION: **BUY/OVERWEIGHT/HOLD/UNDERWEIGHT/SELL**"

## Your Task
Use the available tools to gather data and analyze along FOUR dimensions:

### 1. **Basic On-Chain Metrics**
- Market cap and NVT ratio (Network Value to Transaction ratio)
- Token supply dynamics and exchange flow patterns
- Exchange inflow/outflow dynamics

### 2. **Whale & Market Maker Control Analysis**
- Large holder address concentration: who holds the supply?
- Accumulation vs distribution patterns: are big players buying or dumping?
- Exchange reserve changes: large inflows = bearish, large outflows = bullish
- Assess manipulation risk: is this token dominated by a small number of whales that can rug pull or pump-n-dump?

### 3. **Regulatory & Geopolitical Power Analysis**
- What's the regulatory stance in major jurisdictions?
- Is this asset being accumulation by nation-states or banned?
- How does monetary policy (rate hikes/cuts) affect institutional adoption?
- Are there hidden political agendas affecting this asset's future?

### 4. **Layer Power Game & Capital Flows**
- For Layer 1s: how is the battle for chain dominance going?
- For Layer 2s: who is capturing MEV and liquidity?
- Capital flow between chains: where is money moving in/out?
- Liquidity sharing vs extraction: who benefits from the current architecture?

### 5. **Technical Indicators**
- Pi Cycle Top/Bottom indicators
- Funding rate on perpetual futures
- CME Bitcoin futures gap analysis
- Current Crypto Fear & Greed Index

After gathering all data, write a comprehensive analysis that includes:
   - Basic on-chain trend identification
   - **Power Structure Assessment**: who controls this asset, what are their incentives?
   - **Macro博弈 Implications**: regulation and geopolitics impact
   - Bullish/bearish signals balance including power factors
   - Specific actionable trading conclusions that account for manipulation risk
   - Summary table of key metrics
   - **FINAL DECISION: **BUY/OVERWEIGHT/HOLD/UNDERWEIGHT/SELL**
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
            "onchain_report": report,
        }

    return onchain_analyst_node
