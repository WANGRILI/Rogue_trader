"""Deterministic, auditable resolver for historical natural-language triggers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

import pandas as pd

from roguetrader.backtest.execution_ledger import PlanSpec, ScenarioSpec


PRICE_PATTERN = re.compile(
    r"(?<![\d.])(?:\$\s*)?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{5,6}(?:\.\d+)?)(?![\d.%])"
)


@dataclass(frozen=True)
class TriggerDecision:
    evaluated: bool
    triggered: bool
    evidence_grade: str
    rule_id: str
    source_fields: tuple[str, ...]
    observed: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class PriceRule:
    mode: str
    timeframe: str
    lower: float
    upper: float
    consecutive: int = 1
    alternates: tuple[tuple[float, float], ...] = ()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _price_levels(text: str) -> list[float]:
    result: list[float] = []
    for raw in PRICE_PATTERN.findall(text):
        value = float(raw.replace(",", ""))
        if value >= 10_000 and value not in result:
            result.append(value)
    return result


def _timeframe(text: str) -> str:
    if "周线" in text or "周收盘" in text:
        return "weekly"
    if any(token in text for token in ("日线", "每日收盘", "日收盘", "连续两日", "连续三日")):
        return "daily"
    if "小时线" in text or "小时/4H" in text:
        return "hourly"
    return "intraday"


def _direction(scenario_id: str, text: str) -> str:
    if any(token in text for token in ("未能站上", "未站上", "无法站上")):
        return "below"
    # Condition semantics take precedence over intent-oriented scenario names.
    if any(
        token in text
        for token in (
            "价格进入",
            "价格处于",
            "回抽至",
            "反弹至",
            "回踩",
            "回撤至",
            "回落至",
            "回调至",
            "下探",
            "下跌至",
            "跌至",
            "触及",
            "测试",
            "支撑区",
            "阻力区",
        )
    ):
        return "zone"
    if any(token in text for token in ("站上", "站稳", "高于", "升破", "上涨至", "达到", "突破", "收复")) and not any(
        token in text for token in ("未出现", "未能", "低于", "跌破", "失守")
    ):
        return "above"
    if any(token in text for token in ("低于", "跌破", "失守", "下方", "≤")) and not any(
        token in text for token in ("未跌破", "未收盘跌破", "未确认收盘")
    ):
        return "below"
    if any(token in text for token in ("未跌破", "未收盘跌破", "未确认收盘跌破", "守住", "守稳")):
        return "above"
    if any(token in scenario_id for token in ("breakout", "target", "reclaim", "above", "trend_buy", "trend_build", "extension")):
        return "above"
    if any(token in scenario_id for token in ("below", "breakdown", "hard_stop", "invalidation", "failure", "defense", "exit")):
        return "below"
    return "zone"


def _explicit_close_rule(text: str) -> PriceRule | None:
    """Compile an explicit daily/weekly close clause, if present."""

    markers = (
        ("weekly", ("周线", "周收盘")),
        ("daily", ("日线", "每日收盘", "日收盘")),
    )
    for timeframe, tokens in markers:
        starts = [text.find(token) for token in tokens if token in text]
        if not starts:
            continue
        clause = text[min(starts) :]
        levels = _price_levels(clause)
        if not levels:
            continue
        first_level = PRICE_PATTERN.search(clause)
        direction_context = clause[: first_level.start()] if first_level else clause
        if any(token in direction_context for token in ("未收盘跌破", "未确认收盘跌破", "未跌破")):
            return PriceRule("above", timeframe, levels[0], levels[0])
        if any(token in direction_context for token in ("站上", "站稳", "收复", "高于", "升破", "突破")):
            threshold = max(levels[:2]) if len(levels) > 1 else levels[0]
            return PriceRule("above", timeframe, threshold, threshold)
        if any(token in direction_context for token in ("低于", "跌破", "跌回", "失守", "下方")):
            threshold = min(levels[:2]) if len(levels) > 1 else levels[0]
            return PriceRule("below", timeframe, threshold, threshold)
    return None


def _compile_price(scenario: ScenarioSpec) -> PriceRule | None:
    text = scenario.condition
    if "快速下跌至" in text:
        levels = _price_levels(text[text.find("快速下跌至") :])
        if levels:
            lower = min(levels[:2]) if len(levels) > 1 else levels[0]
            upper = max(levels[:2]) if len(levels) > 1 else levels[0]
            return PriceRule("zone", "intraday", lower, upper)
    retest_at = max(text.find("后回踩"), text.find("并回踩"))
    if retest_at >= 0:
        levels = _price_levels(text[retest_at:])
        if levels:
            lower = min(levels[:2]) if len(levels) > 1 else levels[0]
            upper = max(levels[:2]) if len(levels) > 1 else levels[0]
            return PriceRule("zone", "intraday", lower, upper)
    close_rule = _explicit_close_rule(scenario.condition)
    if close_rule is not None:
        consecutive = 3 if "连续三日" in scenario.condition else 2 if "连续两日" in scenario.condition else 1
        return PriceRule(
            close_rule.mode,
            close_rule.timeframe,
            close_rule.lower,
            close_rule.upper,
            consecutive,
        )
    levels = _price_levels(scenario.condition)
    if not levels:
        levels = _price_levels(scenario.scenario_id.replace("_", ""))
    if not levels:
        return None
    mode = _direction(scenario.scenario_id.lower(), scenario.condition)
    lower = min(levels[:2]) if mode == "zone" and len(levels) > 1 else levels[0]
    upper = max(levels[:2]) if mode == "zone" and len(levels) > 1 else levels[0]
    if mode == "above" and len(levels) > 1 and ("–" in scenario.condition or "-" in scenario.condition or "至" in scenario.condition):
        lower = upper = max(levels[:2])
    if mode == "below" and len(levels) > 1 and ("–" in scenario.condition or "-" in scenario.condition or "至" in scenario.condition):
        lower = upper = min(levels[:2])
    consecutive = 3 if "连续三日" in scenario.condition else 2 if "连续两日" in scenario.condition else 1
    alternates: tuple[tuple[float, float], ...] = ()
    if mode == "zone" and len(levels) >= 4 and "或" in scenario.condition:
        alternates = ((min(levels[2], levels[3]), max(levels[2], levels[3])),)
    timeframe = "intraday" if mode == "zone" else _timeframe(scenario.condition)
    return PriceRule(mode, timeframe, lower, upper, consecutive, alternates)


def _price_evidence_grade(text: str) -> str:
    """Downgrade compound/qualitative price prose to an explicit proxy."""

    levels = _price_levels(text)
    qualitative = (
        "附近",
        "结构性",
        "强力",
        "快速",
        "确认",
        "有效",
        "不追",
        "否则",
    )
    if len(levels) > 2 or "或" in text or any(token in text for token in qualitative):
        return "proxy"
    return "exact"


def _absolute_volume_threshold(text: str) -> float | None:
    billion = re.search(r"(\d+(?:\.\d+)?)\s*B\b", text, re.IGNORECASE)
    if billion:
        return float(billion.group(1)) * 1_000_000_000
    yi = re.search(r"(\d+(?:\.\d+)?)\s*亿(?:美元|USDT)?", text)
    if yi:
        return float(yi.group(1)) * 100_000_000
    return None


def _percent_threshold(text: str) -> float | None:
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", text)
    return float(match.group(1)) / 100 if match else None


class MultiFactorConditionResolver:
    """Resolve every historical condition with exact or declared proxy evidence."""

    def __init__(self, features: pd.DataFrame):
        self.features = features
        self.audit: dict[tuple[str, str], dict[str, Any]] = {}
        self._grade_cache: dict[tuple[str, str], str] = {}

    def _consecutive(
        self,
        timestamp: pd.Timestamp,
        field: str,
        mode: str,
        threshold: float,
        count: int,
    ) -> bool:
        history = self.features.loc[:timestamp, field].dropna().resample("1D").last().dropna()
        if len(history) < count:
            return False
        values = history.iloc[-count:]
        if mode == "above":
            return bool((values >= threshold).all())
        return bool((values <= threshold).all())

    def _price_check(
        self,
        rule: PriceRule,
        timestamp: pd.Timestamp,
        row: pd.Series,
    ) -> tuple[bool, list[str], dict[str, Any]]:
        if rule.timeframe == "weekly":
            value = _finite(row.get("weekly_close"))
            fields = ["weekly_close"]
            if value is None:
                return False, fields, {}
            hit = value >= rule.upper if rule.mode == "above" else value <= rule.lower if rule.mode == "below" else rule.lower <= value <= rule.upper
            return hit, fields, {"weekly_close": value}
        if rule.timeframe == "daily":
            value = _finite(row.get("daily_close"))
            fields = ["daily_close"]
            if value is None:
                return False, fields, {}
            if rule.consecutive > 1 and rule.mode in {"above", "below"}:
                hit = self._consecutive(timestamp, "daily_close", rule.mode, rule.lower, rule.consecutive)
            elif rule.mode == "above":
                hit = value >= rule.upper
            elif rule.mode == "below":
                hit = value <= rule.lower
            else:
                low = _finite(row.get("daily_low"))
                high = _finite(row.get("daily_high"))
                hit = low is not None and high is not None and low <= rule.upper and high >= rule.lower
                fields.extend(["daily_low", "daily_high"])
            return hit, fields, {field: _finite(row.get(field)) for field in fields}
        if rule.timeframe == "hourly":
            value = _finite(row.get("hour_close"))
            fields = ["hour_close"]
            if value is None:
                return False, fields, {}
            hit = value >= rule.upper if rule.mode == "above" else value <= rule.lower if rule.mode == "below" else (
                rule.lower <= value <= rule.upper
                or any(lower <= value <= upper for lower, upper in rule.alternates)
            )
            return hit, fields, {"hour_close": value}
        low = _finite(row.get("hour_low"))
        high = _finite(row.get("hour_high"))
        fields = ["hour_low", "hour_high"]
        if low is None or high is None:
            return False, fields, {}
        if rule.mode == "above":
            hit = high >= rule.upper
        elif rule.mode == "below":
            hit = low <= rule.lower
        else:
            hit = (
                low <= rule.upper and high >= rule.lower
            ) or any(low <= upper and high >= lower for lower, upper in rule.alternates)
        return hit, fields, {"hour_low": low, "hour_high": high}

    def evaluate(
        self,
        plan: PlanSpec,
        scenario: ScenarioSpec,
        timestamp: pd.Timestamp,
        context: Mapping[str, Any],
    ) -> TriggerDecision:
        key = (plan.plan_id, scenario.scenario_id)
        row = self.features.loc[timestamp]
        text = scenario.condition
        checks: list[tuple[str, bool, str, tuple[str, ...], dict[str, Any]]] = []
        price_rule = _compile_price(scenario)
        if price_rule is not None:
            hit, fields, observed = self._price_check(price_rule, timestamp, row)
            checks.append(("price", hit, _price_evidence_grade(text), tuple(fields), observed))

        lower_text = text.lower()
        if any(token in text for token in ("成交量", "放量", "带量", "量能", "缩量")):
            threshold = _absolute_volume_threshold(text)
            value = _finite(row.get("cm_volume_reported_spot_usd_1d"))
            ratio = _finite(row.get("cm_reported_volume_ratio_20"))
            if threshold is not None:
                hit = value is not None and (value <= threshold if "低于" in text else value >= threshold)
                observed = {"reported_volume_usd": value, "threshold": threshold}
            elif "缩量" in text or "萎缩" in text:
                hit = ratio is not None and ratio <= 1.0
                observed = {"reported_volume_ratio_20": ratio}
            else:
                required = 1.2 if any(token in text for token in ("显著", "强成交量", "高成交量", "1.2 倍", "1.2倍", "激增")) else 1.0
                hit = ratio is not None and ratio >= required
                observed = {"reported_volume_ratio_20": ratio, "required": required}
            explicit_ratio = bool(re.search(r"\d+(?:\.\d+)?\s*倍", text))
            grade = "exact" if threshold is not None or explicit_ratio else "proxy"
            if any(token in lower_text for token in ("cme", "binance", "coinbase", "btcc", "pionex", "质量", "可信")):
                grade = "proxy"
            checks.append(("volume", hit, grade, ("cm_volume_reported_spot_usd_1d", "cm_reported_volume_ratio_20"), observed))

        if "ETF" in text.upper() or "净流入" in text or "净流出" in text:
            flow = _finite(row.get("etf_flow_usd_m"))
            day_streak = _finite(row.get("etf_positive_streak"))
            week_streak = _finite(row.get("etf_positive_week_streak"))
            absolute_threshold = _absolute_volume_threshold(text)
            etf_grade = "exact"
            if "连续三日" in text:
                hit = day_streak is not None and day_streak >= 3
            elif "连续两周" in text or "连续第二周" in text:
                hit = week_streak is not None and week_streak >= 2
            elif "连续多日" in text or "持续流入" in text:
                hit = day_streak is not None and day_streak >= 2
                etf_grade = "proxy"
            elif absolute_threshold is not None:
                hit = flow is not None and flow * 1_000_000 >= absolute_threshold
            elif "流出收敛" in text:
                recent = self.features.loc[:timestamp, "etf_flow_usd_m"].dropna().tail(3)
                hit = len(recent) >= 2 and recent.iloc[-1] > recent.iloc[-2]
                etf_grade = "proxy"
            else:
                hit = flow is not None and flow > 0
                if "显著" in text:
                    etf_grade = "proxy"
            checks.append(("etf_flow", hit, etf_grade, ("etf_flow_usd_m", "etf_positive_streak", "etf_positive_week_streak"), {"flow_usd_m": flow, "positive_days": day_streak, "positive_weeks": week_streak, "threshold_usd": absolute_threshold}))

        if "资金费率" in text:
            rate = _finite(row.get("funding_rate"))
            threshold = _percent_threshold(text)
            if "转负" in text or "0 或负" in text or "0或负" in text:
                hit = rate is not None and rate <= 0
            elif "飙升" in text or "极端正" in text:
                recent = self.features.loc[:timestamp, "funding_rate"].dropna().tail(21)
                hit = rate is not None and len(recent) >= 3 and rate >= recent.quantile(0.9)
            else:
                maximum = threshold if threshold is not None else 0.0003
                hit = rate is not None and rate <= maximum
            grade = "exact" if threshold is not None or any(token in text for token in ("转负", "0 或负", "0或负")) else "proxy"
            checks.append(("funding", hit, grade, ("funding_rate",), {"funding_rate": rate, "threshold": threshold}))

        if any(token in text for token in ("恐惧", "贪婪", "情绪降温", "情绪重置")):
            value = _finite(row.get("fear_greed"))
            match = re.search(r"(?:指数)?(?:低于|跌破|≤)\s*(\d{1,3})", text)
            threshold = float(match.group(1)) if match else 25.0 if "极度恐惧" in text else 50.0
            hit = value is not None and value <= threshold
            grade = "exact" if match or "极度恐惧" in text else "proxy"
            checks.append(("sentiment", hit, grade, ("fear_greed",), {"fear_greed": value, "threshold": threshold}))

        if "MVRV" in text.upper():
            value = _finite(row.get("cm_CapMVRVCur"))
            match = re.search(r"MVRV[^\d]*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
            threshold = float(match.group(1)) if match else 1.0
            hit = value is not None and value <= threshold
            checks.append(("mvrv", hit, "exact", ("cm_CapMVRVCur",), {"mvrv": value, "threshold": threshold}))

        if "算力" in text:
            drop = _finite(row.get("cm_hashrate_drop_from_30d_peak"))
            change = _finite(row.get("cm_hashrate_change_3d"))
            hit = change is not None and change >= -0.05 if "企稳" in text else drop is not None and drop <= -0.20
            grade = "proxy" if "企稳" in text else "exact"
            checks.append(("hashrate", hit, grade, ("cm_hashrate_drop_from_30d_peak", "cm_hashrate_change_3d"), {"drop_from_30d_peak": drop, "change_3d": change}))

        if "NVT" in text.upper():
            value = _finite(row.get("nvt_activity_proxy"))
            matches = re.findall(r"\d+(?:\.\d+)?", text[text.upper().find("NVT"):])
            threshold = float(matches[-1]) if matches else 1.0
            checks.append(("nvt_activity_proxy", value is not None and value >= threshold, "proxy", ("nvt_activity_proxy",), {"nvt_activity_proxy": value, "threshold": threshold}))

        if "200日" in text or "200 SMA" in text:
            close = _finite(row.get("daily_close"))
            average = _finite(row.get("daily_sma_200"))
            hit = close is not None and average is not None and close >= average
            checks.append(("sma200", hit, "exact", ("daily_close", "daily_sma_200"), {"close": close, "sma200": average}))
        if "10 EMA" in text or "10日" in text and "均线" in text:
            close = _finite(row.get("daily_close"))
            average = _finite(row.get("daily_ema_10"))
            hit = close is not None and average is not None and close >= average
            checks.append(("ema10", hit, "exact", ("daily_close", "daily_ema_10"), {"close": close, "ema10": average}))
        if "VWMA" in text.upper():
            close = _finite(row.get("daily_close"))
            average = _finite(row.get("daily_vwma_20"))
            hit = close is not None and average is not None and (close < average if "低于" in text else close >= average)
            checks.append(("vwma20", hit, "exact", ("daily_close", "daily_vwma_20"), {"close": close, "vwma20": average}))

        if any(token in text for token in ("企稳", "止跌", "守稳", "支撑有效", "结构性支撑", "强反弹", "止稳", "反转K线")):
            stabilized = _finite(row.get("daily_stabilized"))
            checks.append(("stabilization", stabilized == 1.0, "proxy", ("daily_stabilized",), {"stabilized_proxy": stabilized}))
        if any(token in text for token in ("滞涨", "遭拒", "乏力", "量价背离")):
            rejected = _finite(row.get("daily_bearish_rejection"))
            checks.append(("rejection", rejected == 1.0, "proxy", ("daily_bearish_rejection",), {"rejection_proxy": rejected}))
        if "看涨背离" in text or "RSI 背离" in text:
            divergence = _finite(row.get("daily_bullish_divergence"))
            checks.append(("bullish_divergence", divergence == 1.0, "proxy", ("daily_bullish_divergence",), {"bullish_divergence_proxy": divergence}))
        if "RSI 不再创新低" in text:
            value = _finite(row.get("daily_rsi_14"))
            recent = self.features.loc[:timestamp, "daily_rsi_14"].dropna().resample("1D").last().dropna().tail(5)
            hit = value is not None and len(recent) >= 2 and value > recent.iloc[:-1].min()
            checks.append(("rsi_not_new_low", hit, "proxy", ("daily_rsi_14",), {"rsi14": value}))

        position = float(context.get("position", 0.0))
        equity = float(context.get("equity", 0.0))
        price = float(row["hour_close"])
        exposure = position * price / equity if equity > 0 else 0.0
        if any(token in text for token in ("仓位过重", "超配", "超风险预算", "敞口高于")):
            match = re.search(r"高于总资金的\s*(\d+(?:\.\d+)?)%", text)
            threshold = float(match.group(1)) / 100 if match else 0.60
            checks.append(("portfolio_overweight", exposure > threshold, "exact", ("portfolio_state",), {"exposure": exposure, "threshold": threshold}))
        elif any(token in text for token in ("仍有BTC", "若持有现货多头", "现有战术仓位")):
            checks.append(("has_position", position > 0, "exact", ("portfolio_state",), {"position": position}))
        if any(token in text for token in ("首仓已建立", "已按", "试仓成交", "多头建立后")):
            fills = int(context.get("active_plan_fill_count", 0))
            checks.append(("active_plan_has_fill", fills > 0, "exact", ("execution_state",), {"active_plan_fill_count": fills}))
        if "止盈" in text and "成交后" in text:
            take_profit_fills = int(context.get("active_plan_take_profit_fill_count", 0))
            checks.append(("take_profit_filled", take_profit_fills > 0, "exact", ("execution_state",), {"take_profit_fill_count": take_profit_fills}))

        if "FOMC 前" in text or "FOMC前" in text:
            checks.append(("pre_fomc", _finite(row.get("pre_fomc")) == 1.0, "exact", ("pre_fomc",), {"pre_fomc": _finite(row.get("pre_fomc"))}))
        if "FOMC 后" in text or "FOMC后" in text:
            checks.append(("post_fomc", _finite(row.get("post_fomc")) == 1.0, "exact", ("post_fomc",), {"post_fomc": _finite(row.get("post_fomc"))}))
        if "美联储暂停加息" in text:
            checks.append(("fed_hold", _finite(row.get("fed_hold_confirmed")) == 1.0, "exact", ("fed_hold_confirmed",), {"fed_hold_confirmed": _finite(row.get("fed_hold_confirmed"))}))
        if "CLARITY" in text.upper():
            checks.append(("clarity_vote_window", _finite(row.get("clarity_vote_window")) == 1.0, "proxy", ("clarity_vote_window",), {"clarity_vote_window": _finite(row.get("clarity_vote_window"))}))
        if any(token in text for token in ("事件前", "会议前", "宏观数据")) and "FOMC" not in text:
            checks.append(("event_window", _finite(row.get("event_risk_window")) == 1.0, "proxy", ("event_risk_window",), {"event_risk_window": _finite(row.get("event_risk_window"))}))
        if any(token in text for token in ("宏观风险爆发", "极端宏观事件", "美日汇率急速下挫")):
            daily_return = _finite(row.get("daily_return_1d"))
            volume_ratio = _finite(row.get("cm_reported_volume_ratio_20"))
            shock = daily_return is not None and volume_ratio is not None and daily_return <= -0.05 and volume_ratio >= 1.2
            checks.append(("macro_shock_proxy", shock, "proxy", ("daily_return_1d", "cm_reported_volume_ratio_20"), {"daily_return": daily_return, "volume_ratio": volume_ratio}))

        if any(token in lower_text for token in ("cme", "顶级交易所", "受监管交易所", "高质量机构", "成交量质量")):
            coinbase_ratio = _finite(row.get("coinbase_volume_ratio_20"))
            funding = _finite(row.get("funding_rate"))
            hit = coinbase_ratio is not None and funding is not None and coinbase_ratio >= 1.0 and funding >= 0
            checks.append(("institutional_quality_proxy", hit, "proxy", ("coinbase_volume_ratio_20", "funding_rate"), {"coinbase_volume_ratio": coinbase_ratio, "funding_rate": funding}))
        if "MSTR" in text.upper():
            flow = _finite(row.get("etf_flow_usd_m"))
            checks.append(("mstr_proxy", flow is not None and flow > 0, "proxy", ("etf_flow_usd_m",), {"etf_flow_proxy": flow}))

        if not checks:
            decision = TriggerDecision(
                evaluated=False,
                triggered=False,
                evidence_grade="unavailable",
                rule_id="unparsed_condition",
                source_fields=tuple(),
                observed={},
                reason="condition could not be mapped to auditable evidence",
            )
            self.audit[key] = {
                "plan_id": plan.plan_id,
                "analysis_date": plan.analysis_date,
                "scenario_id": scenario.scenario_id,
                "condition_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "evidence_grade": "unavailable",
                "rule_id": "unparsed_condition",
                "source_fields": "",
                "evaluated": False,
                "triggered": False,
                "trigger_time": "",
                "last_evaluated_at": timestamp.isoformat(),
                "observed_json": "{}",
            }
            self._grade_cache[key] = "unavailable"
            return decision

        price_checks = [item for item in checks if item[0] == "price"]
        other_checks = [item for item in checks if item[0] != "price"]
        if "至少满足两项" in text:
            triggered = bool(price_checks and price_checks[0][1]) and sum(item[1] for item in other_checks) >= 2
        elif "；或" in text or ";或" in text:
            triggered = any(item[1] for item in checks)
        elif "或" in text and not price_checks:
            triggered = any(item[1] for item in checks)
        elif "或" in text and price_checks and other_checks:
            triggered = price_checks[0][1] and any(item[1] for item in other_checks)
        else:
            triggered = all(item[1] for item in checks)
        grade = "proxy" if any(item[2] == "proxy" for item in checks) else "exact"
        fields = tuple(dict.fromkeys(field for item in checks for field in item[3]))
        observed = {item[0]: item[4] for item in checks}
        rule_id = "+".join(item[0] for item in checks)
        missing_fields = tuple(
            field
            for field in fields
            if field not in {"portfolio_state", "execution_state"}
            and _finite(row.get(field)) is None
        )
        evaluated = not missing_fields
        if not evaluated:
            triggered = False
            grade = "unavailable"
        decision = TriggerDecision(
            evaluated=evaluated,
            triggered=triggered,
            evidence_grade=grade,
            rule_id=rule_id,
            source_fields=fields,
            observed=observed,
            reason=(
                "required evidence unavailable"
                if not evaluated
                else "all declared checks met"
                if triggered
                else "condition not met"
            ),
        )
        self.audit[key] = {
            "plan_id": plan.plan_id,
            "analysis_date": plan.analysis_date,
            "scenario_id": scenario.scenario_id,
            "condition_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "evidence_grade": grade,
            "rule_id": rule_id,
            "source_fields": ",".join(fields),
            "evaluated": evaluated,
            "triggered": triggered,
            "trigger_time": timestamp.isoformat() if triggered else "",
            "last_evaluated_at": timestamp.isoformat(),
            "observed_json": json.dumps(observed, ensure_ascii=False, sort_keys=True),
        }
        self._grade_cache[key] = grade
        return decision

    def grade_for(self, plan_id: str, scenario_id: str) -> str:
        return self._grade_cache.get((plan_id, scenario_id), "proxy")

    def audit_frame(self) -> pd.DataFrame:
        if not self.audit:
            return pd.DataFrame()
        return pd.DataFrame(self.audit.values()).sort_values(
            ["analysis_date", "plan_id", "scenario_id"]
        ).reset_index(drop=True)
