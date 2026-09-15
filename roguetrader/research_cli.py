"""Command-line entry point for the research-qualified historical projection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from roguetrader.execution.backfill import discover_historical_decisions, normalize_results_root
from roguetrader.research_integrity import ResearchIntegrityError, build_research_projection
from roguetrader.run_outputs import _atomic_json_write


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _quarantine(items: tuple, event_ids: set[str], reason_code: str, reason: str) -> None:
    found: set[str] = set()
    for item in items:
        if item.record.event_id not in event_ids:
            continue
        path = item.run_dir / "运行清单.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["research_eligibility"] = {
            "status": "quarantined",
            "reason_code": reason_code,
            "reason": reason,
            "scope": "research_and_backtest_only",
            "source_artifact_retained": True,
        }
        _atomic_json_write(path, manifest)
        found.add(item.record.event_id)
    missing = event_ids - found
    if missing:
        raise ResearchIntegrityError(f"未找到待隔离事件：{', '.join(sorted(missing))}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成不改动原始结果的时点安全研究决策与委托投影。"
    )
    parser.add_argument("--results-root", type=Path, default=PROJECT_ROOT / "my_results")
    parser.add_argument("--official-csv", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--quarantine-event-id", action="append", default=[])
    parser.add_argument("--reason-code", default="manual_point_in_time_quarantine")
    parser.add_argument("--reason", default="人工审计确认该结果不满足时点安全要求。")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        root = normalize_results_root(args.results_root)
        items = discover_historical_decisions(
            root,
            tickers=args.ticker,
            date_from=args.date_from,
            date_to=args.date_to,
            official_only=True,
            official_csv=args.official_csv,
        )
        if args.quarantine_event_id:
            _quarantine(
                items,
                set(args.quarantine_event_id),
                args.reason_code,
                args.reason,
            )
        output = args.output_root or root / "研究"
        report = build_research_projection(items, output_root=output)
    except (ResearchIntegrityError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"research projection error: {exc}") from None
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
