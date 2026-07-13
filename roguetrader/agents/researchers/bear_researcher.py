from langchain_core.messages import AIMessage
import time
import json

from roguetrader.agents.utils.agent_utils import render_agent_prompt


def create_bear_researcher(llm, memory, agent_registry=None):
    def bear_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bear_history = investment_debate_state.get("bear_history", "")

        current_response = investment_debate_state.get("current_response", "")
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        onchain_report = state.get("onchain_report", "")

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}\n\n{onchain_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        prompt = render_agent_prompt(
            agent_registry,
            "bear_researcher",
            "You are a Bear Analyst arguing against investing in the asset.",
            "Build a strong, evidence-based bearish case and counter bullish arguments effectively.",
            "Engage conversationally and debate directly rather than only listing data.",
            f"""Your task is to build a strong, evidence-based case highlighting risks, competitive disadvantages, and negative market indicators. Leverage the provided research and data to address concerns and counter bullish arguments effectively.

Key points to focus on:
- Risk Factors: Highlight potential threats, market volatility, regulatory challenges, and downside risks.
- Competitive Disadvantages: Emphasize factors like weak positioning, declining market share, or structural vulnerabilities.
- Negative Indicators: Use financial weaknesses, unfavorable industry trends, and recent negative news as evidence.
- Bull Counterpoints: Critically analyze the bull argument with specific data and sound reasoning, showing why the bear perspective holds stronger merit.
- Engagement: Present your argument in a conversational style, engaging directly with the bull analyst's points and debating effectively rather than just listing data.

Resources available:
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
Fundamentals report: {fundamentals_report}
On-chain analysis report: {onchain_report}
Conversation history of the debate: {history}
Last bull argument: {current_response}
Reflections from similar situations and lessons learned: {past_memory_str}
Use this information to deliver a compelling bear argument, refute the bull's claims, and engage in a dynamic debate that demonstrates the strengths of the bear position. You must also address reflections and learn from lessons and mistakes you made in the past.
""",
        )

        response = llm.invoke(prompt)

        argument = f"Bear Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bear_node
