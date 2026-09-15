from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from roguetrader.backtest.condition_resolver import MultiFactorConditionResolver, _compile_price
from roguetrader.backtest.execution_ledger import (
    OrderSpec,
    PlanSpec,
    ScenarioSpec,
    run_backtest,
)
from roguetrader.backtest.factor_data import (
    parse_coinbase,
    parse_coinmetrics,
    parse_farside,
    parse_fear_greed,
)


class FactorParserTests(unittest.TestCase):
    def test_parsers_assign_conservative_availability(self):
        fear = parse_fear_greed(
            {
                "data": [
                    {"timestamp": "1788998400", "value": "20", "value_classification": "Fear"},
                    {"timestamp": "1789084800", "value": "25", "value_classification": "Fear"},
                ],
                "metadata": {"error": None},
            }
        )
        self.assertTrue((fear["time"] == fear["available_at"]).all())

        etf = parse_farside(
            "<table><tr><th>Date</th><th>Total</th></tr>"
            "<tr><td>10 Sep 2026</td><td>(123.4)</td></tr></table>"
        )
        self.assertEqual(etf.iloc[0]["etf_flow_usd_m"], -123.4)
        self.assertEqual(
            etf.iloc[0]["available_at"] - etf.iloc[0]["time"],
            pd.Timedelta(days=1),
        )

        metric = parse_coinmetrics(
            {
                "data": [
                    {"time": "2026-09-10T00:00:00Z", "CapMVRVCur": "1.2"},
                    {"time": "2026-09-11T00:00:00Z", "CapMVRVCur": "1.3"},
                ]
            },
            "CapMVRVCur",
        )
        self.assertTrue(
            ((metric["available_at"] - metric["time"]) == pd.Timedelta(days=1)).all()
        )

        candles = parse_coinbase(
            [
                [1788998400, 90, 110, 100, 105, 10],
                [1789084800, 100, 120, 105, 115, 12],
            ]
        )
        self.assertEqual(candles.iloc[0]["volume_usd"], 1050)


def _manual_plan() -> PlanSpec:
    order = OrderSpec(
        event_id="event-order",
        order_id="sell-on-fear",
        sequence=1,
        side="sell",
        intent="reduce",
        order_type="market",
        size_basis="current_position_pct",
        size_value=0.5,
        limit_price=None,
        stop_price=None,
        take_profit=None,
        stop_loss=None,
        time_in_force="GTC",
        after_order_id=None,
    )
    scenario = ScenarioSpec(
        scenario_id="fear_reduce",
        priority=1,
        exclusive_group=None,
        trigger_type="manual_confirmation",
        trigger_operator=None,
        trigger_value=None,
        condition="恐惧与贪婪指数低于25",
        orders=(order,),
    )
    return PlanSpec(
        plan_id="plan-1",
        decision_event_id="decision-1",
        analysis_date="2026-09-10",
        ticker="BTC-USD",
        action="REDUCE",
        generated_at=pd.Timestamp("2026-09-10T00:00:00Z"),
        generated_at_source="decision_generated_at",
        valid_until=pd.Timestamp("2026-09-10T04:00:00Z"),
        max_position_pct=1.0,
        max_order_cash_pct=1.0,
        scenarios=(scenario,),
    )


class MultiFactorExecutionTests(unittest.TestCase):
    def test_price_compiler_uses_condition_semantics_not_intent_name(self):
        def scenario(identifier: str, condition: str) -> ScenarioSpec:
            return ScenarioSpec(
                scenario_id=identifier,
                priority=1,
                exclusive_group=None,
                trigger_type="manual_confirmation",
                trigger_operator=None,
                trigger_value=None,
                condition=condition,
                orders=(),
            )

        rebound = _compile_price(
            scenario("rebound_exit", "价格回抽至50日均线约64,800美元附近")
        )
        self.assertEqual((rebound.mode, rebound.timeframe, rebound.lower), ("zone", "intraday", 64_800))

        rebuild = _compile_price(
            scenario("invalidation_rebuild", "BTC-USD 日线收盘价放量收复 78,500，看空逻辑失效")
        )
        self.assertEqual((rebuild.mode, rebuild.timeframe, rebuild.lower), ("above", "daily", 78_500))

        weekly = _compile_price(
            scenario("weekly_reduce", "未能反弹至 66,000–67,500，且周线收盘低于 62,000")
        )
        self.assertEqual((weekly.mode, weekly.timeframe, weekly.lower), ("below", "weekly", 62_000))

        pullback = _compile_price(
            scenario("add2_73500_74000", "BTC-USD 回撤至 73,500–74,000，且守住 200 日均线")
        )
        self.assertEqual((pullback.mode, pullback.timeframe, pullback.lower, pullback.upper), ("zone", "intraday", 73_500, 74_000))

        false_breakout = _compile_price(
            scenario("false_breakout", "BTC-USD 突破 82,300 后快速收回，且日线收盘跌回 79,000 美元下方")
        )
        self.assertEqual((false_breakout.mode, false_breakout.timeframe, false_breakout.lower), ("below", "daily", 79_000))

        value_buy = _compile_price(
            scenario("value_buy", "价格快速下跌至58,500-60,000区间，并在日线上以强反弹收复60,000")
        )
        self.assertEqual((value_buy.mode, value_buy.timeframe, value_buy.lower, value_buy.upper), ("zone", "intraday", 58_500, 60_000))

        retest = _compile_price(
            scenario("breakout_retest", "BTC-USD 突破 $81,000 后回踩 $78,000–$79,000 守稳")
        )
        self.assertEqual((retest.mode, retest.timeframe, retest.lower, retest.upper), ("zone", "intraday", 78_000, 79_000))

    def test_missing_required_feature_is_not_counted_as_evaluated(self):
        index = pd.date_range("2026-09-10T00:00:00Z", periods=3, freq="1h")
        market = pd.DataFrame(
            {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            index=index,
        )
        features = pd.DataFrame(
            {"hour_close": market["close"], "daily_close": 100.0, "daily_sma_200": float("nan")},
            index=index,
        )
        plan = _manual_plan()
        sma_scenario = ScenarioSpec(
            scenario_id="sma_entry",
            priority=1,
            exclusive_group=None,
            trigger_type="manual_confirmation",
            trigger_operator=None,
            trigger_value=None,
            condition="价格守住 200 日均线",
            orders=plan.scenarios[0].orders,
        )
        plan = PlanSpec(**{**plan.__dict__, "scenarios": (sma_scenario,)})
        result = run_backtest(
            (plan,), market, initial_balance=10_000, fee_rate=0, slippage_rate=0,
            condition_resolver=MultiFactorConditionResolver(features),
        )
        self.assertEqual(result["metrics"]["condition_evaluation_coverage"], 0.0)
        self.assertFalse(bool(result["condition_audit"].iloc[0]["evaluated"]))

    def test_exact_condition_fills_only_on_next_bar(self):
        index = pd.date_range("2026-09-10T00:00:00Z", periods=4, freq="1h")
        market = pd.DataFrame(
            {
                "open": [100, 100, 90, 80],
                "high": [101, 101, 91, 81],
                "low": [99, 99, 89, 79],
                "close": [100, 100, 90, 80],
                "volume": [1, 1, 1, 1],
            },
            index=index,
        )
        features = pd.DataFrame(
            {
                "hour_close": market["close"],
                "fear_greed": [50, 20, 20, 20],
            },
            index=index,
        )
        resolver = MultiFactorConditionResolver(features)
        result = run_backtest(
            (_manual_plan(),),
            market,
            initial_balance=10_000,
            fee_rate=0,
            slippage_rate=0,
            condition_resolver=resolver,
        )

        self.assertEqual(result["metrics"]["fill_count"], 1)
        self.assertEqual(result["fills"].iloc[0]["time"], "2026-09-10T02:00:00+00:00")
        self.assertEqual(result["metrics"]["manual_exact_order_count"], 1)
        self.assertEqual(result["metrics"]["condition_evaluation_coverage"], 1.0)
        self.assertTrue(bool(result["condition_audit"].iloc[0]["triggered"]))

    def test_never_met_condition_keeps_last_observation(self):
        index = pd.date_range("2026-09-10T00:00:00Z", periods=3, freq="1h")
        market = pd.DataFrame(
            {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            index=index,
        )
        features = pd.DataFrame(
            {"hour_close": market["close"], "fear_greed": [50, 50, 50]},
            index=index,
        )
        result = run_backtest(
            (_manual_plan(),),
            market,
            initial_balance=10_000,
            fee_rate=0,
            slippage_rate=0,
            condition_resolver=MultiFactorConditionResolver(features),
        )
        self.assertEqual(
            result["order_status"].iloc[0]["status"],
            "condition_not_met_at_data_end",
        )
        self.assertEqual(
            result["condition_audit"].iloc[0]["last_evaluated_at"],
            "2026-09-10T02:00:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
