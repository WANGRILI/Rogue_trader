"""LLM translator from a portfolio decision to a small execution-plan draft."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from roguetrader.agents.utils.agent_utils import render_agent_prompt

from .models import (
    PlanValidationError,
    normalize_execution_plan_draft,
    validate_execution_plan,
)


class PlannerOutputError(ValueError):
    """Raised when the execution planner does not return the required JSON."""


def find_previous_execution_plan(
    results_dir: str | Path,
    *,
    ticker: str,
    analysis_date: str,
    runtime_mode: str | None = None,
    exclude_run_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    """Return the latest completed prior plan for the same ticker and lane."""

    run_root = Path(results_dir) / "运行结果"
    if not run_root.is_dir():
        return None
    excluded = Path(exclude_run_dir).resolve() if exclude_run_dir else None
    lane = {"production": "prod", "development": "dev"}.get(
        str(runtime_mode or "").lower(), str(runtime_mode or "").lower()
    )
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for plan_path in run_root.glob("*/执行计划.json"):
        if excluded is not None and plan_path.parent.resolve() == excluded:
            continue
        try:
            if plan_path.stat().st_size > 2 * 1024 * 1024:
                continue
            raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            if not isinstance(raw_plan, dict):
                continue
            if raw_plan.get("ticker") != ticker:
                continue
            plan_analysis_date = str(raw_plan.get("analysis_date") or "")
            if not plan_analysis_date or plan_analysis_date > analysis_date:
                continue
            manifest_path = plan_path.parent / "运行清单.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("status") != "completed":
                continue
            if lane and str(manifest.get("runtime_mode") or "").lower() != lane:
                continue
            plan = validate_execution_plan(raw_plan)
            candidates.append(
                (str(plan.get("created_at") or ""), plan_path.parent.name, plan)
            )
        except (OSError, json.JSONDecodeError, PlanValidationError):
            continue
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _json_object(content: Any) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise PlannerOutputError("执行规划器未返回文本。")
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise PlannerOutputError("执行规划器未返回 JSON 对象。")
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise PlannerOutputError(f"执行计划 JSON 无法解析：{exc.msg}。") from exc
    if not isinstance(value, dict):
        raise PlannerOutputError("执行计划必须是 JSON 对象。")
    return value


class ExecutionPlanner:
    """A non-voting agent that translates, but never changes, the final rating."""

    def __init__(self, llm: Any, agent_registry: Any = None):
        self.llm = llm
        self.agent_registry = agent_registry

    def create_draft(
        self,
        *,
        ticker: str,
        analysis_date: str,
        action: str,
        final_decision_text: str,
        previous_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        schema_example = {
            "continuity_action": "baseline",
            "change_summary": "First plan; no prior instructions to reconcile.",
            "plan_summary": "One short summary",
            "valid_for_hours": 24,
            "risk_limits": {
                "max_position_pct": 0.5,
                "max_order_cash_pct": 0.25,
            },
            "scenarios": [
                {
                    "scenario_id": "base_case",
                    "priority": 1,
                    "exclusive_group": None,
                    "trigger": {"type": "immediate"},
                    "orders": [
                        {
                            "order_id": "entry_1",
                            "sequence": 1,
                            "side": "buy",
                            "intent": "open",
                            "order_type": "limit",
                            "size": {"basis": "available_cash_pct", "value": 0.1},
                            "limit_price": 100.0,
                            "stop_price": None,
                            "take_profit": 110.0,
                            "stop_loss": 95.0,
                            "time_in_force": "GTC",
                            "after_order_id": None,
                        }
                    ],
                }
            ],
        }
        previous_context = "No previous execution plan exists. Use continuity_action baseline."
        if previous_plan is not None:
            previous_context = json.dumps(
                {
                    "plan_id": previous_plan["plan_id"],
                    "analysis_date": previous_plan["analysis_date"],
                    "action": previous_plan["action"],
                    "valid_until": previous_plan["valid_until"],
                    "plan_summary": previous_plan["plan_summary"],
                    "scenarios": previous_plan["scenarios"],
                },
                ensure_ascii=False,
                indent=2,
            )
        prompt = render_agent_prompt(
            self.agent_registry,
            "execution_planner",
            "You are RogueTrader's non-voting Execution Planner.",
            "Translate the final portfolio decision into a compact parameterized spot plan without changing its rating or inventing price levels.",
            "Return strict JSON only. Prefer a few clear scenarios and orders over unnecessary complexity.",
            f"""Create a paper-trading execution-plan draft for {ticker} as of {analysis_date}.

Final rating: {action}

Final portfolio decision:
{final_decision_text}

Previous execution plan:
{previous_context}

Rules:
- This is SPOT and PAPER only. Never create leverage, margin, derivatives, or short-selling instructions.
- If a previous plan exists, continuity_action must be retain, amend, or replace. Explicitly account for its unexpired instructions in change_summary; say which are retained, cancelled, or replaced. Never assume that an earlier order filled.
- If no previous plan exists, continuity_action must be baseline.
- The new scenarios list is the complete authoritative snapshot, never an incremental patch. Reproduce every prior instruction that remains valid; any prior instruction not carried forward is treated as cancelled or replaced.
- Use the same language as the final portfolio decision for plan_summary, change_summary, and manual condition text.
- Use one or more scenarios. A trigger is immediate, last_price, or {{"type":"manual_confirmation","condition":"..."}}.
- Use manual_confirmation for daily/weekly closes, indicators, news, time windows, or any condition that cannot be determined from last_price alone. Never weaken those conditions into last_price.
- Use only numeric price levels explicitly present in the final decision. Never invent a missing price.
- A buy order may use intent open/increase and size basis available_cash_pct/fixed_quantity.
- A sell order may use intent reduce/exit and size basis current_position_pct/fixed_quantity.
- Treat current_position_pct and available_cash_pct as symbolic instructions based on X_POSITION and X_CASH. The daily analysis must not request actual account values.
- Supported order types: market, limit, stop_market, stop_limit.
- market uses neither price field; limit requires limit_price; stop_market requires stop_price; stop_limit requires both.
- Use fixed_quantity only when the final decision explicitly states an absolute quantity.
- Use after_order_id only for a later order in the same scenario.
- If the decision calls for no order and gives no objective trigger, return one immediate scenario with an empty orders array.
- Keep valid_for_hours between 1 and 168, scenarios at most 8, and orders per scenario at most 6.
- Do not include account balances, current positions, explanations outside JSON, or fields absent from this schema.

Required JSON shape:
{json.dumps(schema_example, ensure_ascii=False, indent=2)}""",
        )
        response = self.llm.invoke(prompt)
        try:
            draft = _json_object(response.content)
            if previous_plan is None:
                draft["previous_plan_id"] = None
                draft["continuity_action"] = "baseline"
            else:
                draft["previous_plan_id"] = previous_plan["plan_id"]
            return normalize_execution_plan_draft(draft)
        except PlanValidationError as exc:
            raise PlannerOutputError(str(exc)) from exc
