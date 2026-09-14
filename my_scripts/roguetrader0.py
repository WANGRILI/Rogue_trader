"""Manual RogueTrader run script.

Use this script for ad-hoc runs where you want to choose the ticker, date,
analysts, language, and debate depth from the command line. The daily scheduler
entrypoint is intentionally kept in roguetrader1.py.
"""

import argparse
import datetime
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from roguetrader.default_config import DEFAULT_CONFIG  # noqa: E402
from roguetrader.graph.trading_graph import RogueTraderGraph  # noqa: E402


DEFAULT_ANALYSTS = "market,social,news,fundamentals,onchain"


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_analysts(value: str) -> list[str]:
    analysts = [item.strip() for item in value.split(",") if item.strip()]
    return analysts or DEFAULT_ANALYSTS.split(",")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run RogueTrader manually.")
    parser.add_argument("--ticker", default="BTC-USD", help="Ticker symbol to analyze.")
    parser.add_argument(
        "--date",
        default=datetime.date.today().isoformat(),
        help="Analysis date in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--analysts",
        default=DEFAULT_ANALYSTS,
        help="Comma-separated analysts: market,social,news,fundamentals,onchain.",
    )
    parser.add_argument("--output-language", default="Chinese", help="Report language.")
    parser.add_argument("--max-debate-rounds", type=int, default=2)
    parser.add_argument("--max-risk-discuss-rounds", type=int, default=1)
    parser.add_argument("--max-recur-limit", type=int, default=50)
    parser.add_argument("--quick-model", default="deepseek-v4-flash")
    parser.add_argument("--deep-model", default="deepseek-v4-pro")
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--backend-url", default="https://api.deepseek.com")
    parser.add_argument(
        "--execution-plan",
        action=argparse.BooleanOptionalAction,
        default=env_flag("ROGUETRADER_EXECUTION_PLAN_ENABLED"),
        help="Generate a parameterized spot paper-execution plan.",
    )
    parser.add_argument(
        "--run-mode",
        choices=("production", "development", "prod", "dev"),
        default=os.getenv("ROGUETRADER_RUNTIME_MODE", "development"),
        help="Runtime lane recorded in the run manifest.",
    )
    parser.add_argument(
        "--trigger",
        choices=("scheduled", "recovery", "manual"),
        default=os.getenv("ROGUETRADER_RUN_TRIGGER", "manual"),
        help="Reason this run was started.",
    )
    parser.add_argument(
        "--scheduled-for",
        default=os.getenv("ROGUETRADER_SCHEDULED_FOR"),
        help="Original scheduled timestamp, when applicable.",
    )
    parser.add_argument(
        "--parent-run-id",
        default=os.getenv("ROGUETRADER_PARENT_RUN_ID"),
        help="Failed or superseded run that led to this recovery run.",
    )
    parser.add_argument(
        "--no-debug",
        action="store_true",
        help="Disable LangGraph debug streaming.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = args.provider
    config["backend_url"] = args.backend_url
    config["deep_think_llm"] = args.deep_model
    config["quick_think_llm"] = args.quick_model
    config["results_dir"] = str(PROJECT_ROOT / "my_results")
    config["output_language"] = args.output_language
    config["max_debate_rounds"] = args.max_debate_rounds
    config["max_risk_discuss_rounds"] = args.max_risk_discuss_rounds
    config["max_recur_limit"] = args.max_recur_limit
    config["execution_plan_enabled"] = args.execution_plan
    config["run_context"] = {
        "runtime_mode": args.run_mode,
        "trigger": args.trigger,
        "scheduled_for": args.scheduled_for,
        "parent_run_id": args.parent_run_id,
    }

    selected_analysts = parse_analysts(args.analysts)
    rt = RogueTraderGraph(
        debug=not args.no_debug,
        config=config,
        selected_analysts=selected_analysts,
    )
    _, decision = rt.propagate(args.ticker, args.date)

    print(decision)
    if rt.current_output_paths:
        print(f"Output directory: {rt.current_output_paths.root}")


if __name__ == "__main__":
    main()
