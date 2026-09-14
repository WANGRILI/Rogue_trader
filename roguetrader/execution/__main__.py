"""Bind a parameterized execution plan to a minimal paper-account snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from roguetrader.run_outputs import _atomic_json_write

from .models import PlanValidationError, RuntimeState, bind_execution_plan


def _json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PlanValidationError(f"执行计划不存在：{path}")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise PlanValidationError("执行计划文件过大。")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanValidationError(f"无法读取执行计划：{exc}") from exc
    if not isinstance(value, dict):
        raise PlanValidationError("执行计划必须是 JSON 对象。")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bind an execution plan for paper simulation; never submits orders."
    )
    parser.add_argument(
        "plan_path", type=Path, metavar="计划路径", help="运行目录或执行计划.json"
    )
    parser.add_argument("--position-qty", required=True)
    parser.add_argument("--available-cash", required=True)
    parser.add_argument("--last-price", required=True)
    parser.add_argument("--open-buy-qty", default="0")
    parser.add_argument("--open-sell-qty", default="0")
    parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    supplied = args.plan_path.expanduser().resolve()
    plan_path = supplied / "执行计划.json" if supplied.is_dir() else supplied
    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else plan_path.parent / "执行实例.json"
    )
    try:
        plan = _json_object(plan_path)
        state = RuntimeState.from_dict(
            {
                "position_qty": args.position_qty,
                "available_cash": args.available_cash,
                "last_price": args.last_price,
                "open_buy_qty": args.open_buy_qty,
                "open_sell_qty": args.open_sell_qty,
            }
        )
        instance = bind_execution_plan(plan, state)
        _atomic_json_write(output_path, instance)
        if output_path.parent == plan_path.parent:
            index_path = plan_path.parent / "运行索引.json"
            if index_path.is_file():
                index = _json_object(index_path)
                files = index.get("files")
                if isinstance(files, dict):
                    files["execution_instance"] = output_path.name
                    index["execution_instance"] = {"status": instance["status"]}
                    _atomic_json_write(index_path, index)
    except PlanValidationError as exc:
        raise SystemExit(f"execution plan error: {exc}") from None
    print(f"status={instance['status']}")
    print(f"output={output_path}")
    print("execution_mode=paper auto_submit=false")


if __name__ == "__main__":
    main()
