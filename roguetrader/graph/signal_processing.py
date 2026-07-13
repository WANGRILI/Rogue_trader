from langchain_openai import ChatOpenAI
import re

from roguetrader.agents.utils.agent_utils import render_agent_prompt


DECISION_WORD_PATTERN = re.compile(
    r"\b(UNDERWEIGHT|OVERWEIGHT|SELL|BUY|HOLD)\b",
    re.IGNORECASE,
)
LABELED_DECISION_PATTERN = re.compile(
    r"(?:FINAL\s+DECISION|DECISION|RATING|RECOMMENDATION)\s*[:：-]?\s*\**\s*"
    r"(UNDERWEIGHT|OVERWEIGHT|SELL|BUY|HOLD)\b",
    re.IGNORECASE,
)


class SignalProcessor:
    """Processes trading signals to extract actionable decisions."""

    def __init__(self, quick_thinking_llm: ChatOpenAI, agent_registry=None):
        """Initialize with an LLM for processing."""
        self.quick_thinking_llm = quick_thinking_llm
        self.agent_registry = agent_registry

    def process_signal(self, full_signal: str) -> str:
        """
        Process a full trading signal to extract the core decision.

        Args:
            full_signal: Complete trading signal text

        Returns:
            Extracted rating (BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, or SELL)
        """
        if not full_signal:
            return "HOLD"

        match = LABELED_DECISION_PATTERN.search(full_signal)
        if match:
            return match.group(1).upper()

        matches = DECISION_WORD_PATTERN.findall(full_signal)
        if matches:
            return matches[-1].upper()

        messages = [
            (
                "system",
                render_agent_prompt(
                    self.agent_registry,
                    "signal_processor",
                    "You are an efficient assistant that extracts trading decisions from analyst reports.",
                    "Extract the rating as exactly one of: BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, SELL.",
                    "Output only the single rating word, nothing else.",
                    "Extract the rating as exactly one of: BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, SELL. Output only the single rating word, nothing else.",
                ),
            ),
            ("human", full_signal),
        ]

        return self.quick_thinking_llm.invoke(messages).content
