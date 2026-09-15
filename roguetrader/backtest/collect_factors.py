"""CLI for collecting immutable multi-factor backtest evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from roguetrader.backtest.execution_ledger import DEFAULT_LEDGER, load_execution_ledger
from roguetrader.backtest.factor_data import (
    DEFAULT_FACTOR_ROOT,
    FactorDataError,
    collect_factor_bundle,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="采集 ETF、情绪、资金费率、链上与成交量历史因子。"
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_FACTOR_ROOT)
    parser.add_argument("--ticker", default="BTC-USD")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        plans = load_execution_ledger(args.ledger, args.ticker)
        target = collect_factor_bundle(
            start=pd.Timestamp(min(plan.generated_at for plan in plans)),
            end=pd.Timestamp(max(plan.valid_until for plan in plans)),
            output_root=args.output_root,
            timeout=args.timeout,
        )
    except FactorDataError as exc:
        raise SystemExit(f"factor collection error: {exc}") from None
    print(
        json.dumps(
            {"output": str(target), "ticker": args.ticker, "plans": len(plans)},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
