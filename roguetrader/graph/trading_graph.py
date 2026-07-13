import os
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Dict, Any, List, Optional

from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage

from roguetrader.agents import *
from roguetrader.default_config import DEFAULT_CONFIG
from roguetrader.agents.utils.memory import FinancialSituationMemory
from roguetrader.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from roguetrader.dataflows.config import set_config

from roguetrader.agents.utils.agent_utils import (
    get_stock_data,
    get_indicators,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_news,
    get_insider_transactions,
    get_global_news,
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
    get_crypto_sentiment,
    get_local_crypto_ohlcv,
)

from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor
from roguetrader.output_paths import make_run_output_paths
from roguetrader.run_outputs import state_snapshot, write_run_outputs
from roguetrader.llm_clients.agent_registry import AgentLLMRegistry


class _TeeStream:
    """Write terminal output to both the original stream and a log file."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return any(getattr(stream, "isatty", lambda: False)() for stream in self.streams)

    def __getattr__(self, name):
        return getattr(self.streams[0], name)


class RogueTraderGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=["market", "social", "news", "fundamentals"],
        debug=False,
        config: Dict[str, Any] = None,
        callbacks: Optional[List] = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Optional list of callback handlers (e.g., for tracking LLM/tool stats)
        """
        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []
        self.selected_analysts = list(selected_analysts)
        self.current_output_paths = None

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(
            os.path.join(self.config["project_dir"], "dataflows/data_cache"),
            exist_ok=True,
        )

        # Initialize LLMs with provider-specific thinking configuration
        llm_kwargs = self._get_provider_kwargs()

        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        self.agent_registry = AgentLLMRegistry(self.config, llm_kwargs)
        self.deep_thinking_llm = self.agent_registry.get_llm("_default_deep", "deep")
        self.quick_thinking_llm = self.agent_registry.get_llm("_default_quick", "quick")

        # Initialize memories
        self.bull_memory = FinancialSituationMemory("bull_memory", self.config)
        self.bear_memory = FinancialSituationMemory("bear_memory", self.config)
        self.trader_memory = FinancialSituationMemory("trader_memory", self.config)
        self.invest_judge_memory = FinancialSituationMemory("invest_judge_memory", self.config)
        self.portfolio_manager_memory = FinancialSituationMemory("portfolio_manager_memory", self.config)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.bull_memory,
            self.bear_memory,
            self.trader_memory,
            self.invest_judge_memory,
            self.portfolio_manager_memory,
            self.conditional_logic,
            self.agent_registry,
        )

        self.propagator = Propagator(max_recur_limit=self.config["max_recur_limit"])
        self.reflector = Reflector(
            self.agent_registry.get_llm("reflector", "quick"),
            self.agent_registry,
        )
        self.signal_processor = SignalProcessor(
            self.agent_registry.get_llm("signal_processor", "quick"),
            self.agent_registry,
        )

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Set up the graph
        self.graph = self.graph_setup.setup_graph(selected_analysts)

    def _get_provider_kwargs(self) -> Dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation."""
        kwargs = {}
        provider = self.config.get("llm_provider", "").lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        return kwargs

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    get_stock_data,
                    get_indicators,
                ]
            ),
            "social": ToolNode(
                [
                    get_news,
                    get_crypto_sentiment,
                ]
            ),
            "news": ToolNode(
                [
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                ]
            ),
            "onchain": ToolNode(
                [
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
            ),
        }

    def propagate(self, company_name, trade_date):
        """Run the trading agents graph for a company on a specific date."""

        self.ticker = company_name
        self.current_output_paths = make_run_output_paths(
            self.config.get("results_dir", "my_results"),
            company_name,
        )
        self.current_output_paths.root.mkdir(parents=True, exist_ok=True)

        # Initialize state
        init_agent_state = self.propagator.create_initial_state(
            company_name, trade_date
        )
        args = self.propagator.get_graph_args()
        final_state = None
        decision = None

        with self.current_output_paths.log_path.open("a", encoding="utf-8") as log_file:
            stdout = _TeeStream(sys.stdout, log_file)
            stderr = _TeeStream(sys.stderr, log_file)
            with redirect_stdout(stdout), redirect_stderr(stderr):
                try:
                    if self.debug:
                        # Debug mode with tracing
                        trace = []
                        for chunk in self.graph.stream(init_agent_state, **args):
                            final_state = chunk
                            if len(chunk["messages"]) == 0:
                                pass
                            else:
                                last_message = chunk["messages"][-1]
                                is_internal_continue = (
                                    isinstance(last_message, HumanMessage)
                                    and last_message.content == "Continue"
                                )
                                if not is_internal_continue:
                                    last_message.pretty_print()
                                trace.append(chunk)

                        final_state = trace[-1] if trace else final_state
                    else:
                        # Standard mode without tracing
                        final_state = self.graph.invoke(init_agent_state, **args)

                    # Store current state for reflection
                    self.curr_state = final_state

                    decision = self.process_signal(final_state["final_trade_decision"])
                    print(f"\nRogueTrader final decision: {decision}")

                    # Log state and normalized run outputs
                    self._log_state(trade_date, final_state, decision)
                except Exception:
                    traceback.print_exc()
                    if final_state is not None:
                        self._log_state(trade_date, final_state, "INCOMPLETE")
                        print(f"Partial outputs written to: {self.current_output_paths.root}")
                    raise
                except KeyboardInterrupt:
                    print("\nRogueTrader run interrupted by user.")
                    if final_state is not None:
                        self._log_state(trade_date, final_state, "INCOMPLETE")
                        print(f"Partial outputs written to: {self.current_output_paths.root}")
                    raise

        return final_state, decision

    def _log_state(self, trade_date, final_state, decision=None):
        """Log the final state to a JSON file."""
        snapshot = state_snapshot(final_state)
        self.log_states_dict[str(trade_date)] = snapshot

        if self.current_output_paths is None:
            self.current_output_paths = make_run_output_paths(
                self.config.get("results_dir", "my_results"),
                self.ticker,
            )

        write_run_outputs(
            paths=self.current_output_paths,
            ticker=self.ticker,
            trade_date=str(trade_date),
            final_state=final_state,
            decision=decision or self.process_signal(final_state["final_trade_decision"]),
            config=self.config,
            selected_analysts=self.selected_analysts,
        )

    def reflect_and_remember(self, returns_losses):
        """Reflect on decisions and update memory based on returns."""
        self.reflector.reflect_bull_researcher(
            self.curr_state, returns_losses, self.bull_memory
        )
        self.reflector.reflect_bear_researcher(
            self.curr_state, returns_losses, self.bear_memory
        )
        self.reflector.reflect_trader(
            self.curr_state, returns_losses, self.trader_memory
        )
        self.reflector.reflect_invest_judge(
            self.curr_state, returns_losses, self.invest_judge_memory
        )
        self.reflector.reflect_portfolio_manager(
            self.curr_state, returns_losses, self.portfolio_manager_memory
        )

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)
