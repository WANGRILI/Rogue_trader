import json
import tempfile
import unittest
from pathlib import Path

from cli.main import save_report_to_disk
from roguetrader.output_paths import (
    graph_state_log_path,
    make_evaluation_output_paths,
    make_run_output_paths,
    safe_symbol,
)
from roguetrader.run_outputs import write_run_outputs


class OutputPathTests(unittest.TestCase):
    def test_safe_symbol_normalizes_common_crypto_tickers(self):
        self.assertEqual(safe_symbol("BTC-USD"), "BTC_USD")
        self.assertEqual(safe_symbol("ETH/USDT"), "ETH_USDT")

    def test_run_and_evaluation_paths_use_chinese_structure(self):
        root = Path("/tmp/roguetrader-output-test")

        run_paths = make_run_output_paths(root, "BTC-USD", "20260712_120000")
        self.assertEqual(
            run_paths.report_path,
            root / "运行结果" / "20260712_120000_BTC_USD" / "报告.md",
        )
        self.assertEqual(run_paths.state_path.name, "状态.json")
        self.assertEqual(run_paths.index_path.name, "运行索引.json")
        self.assertEqual(run_paths.decision_path.name, "最终决策.json")
        self.assertEqual(run_paths.config_path.name, "运行配置.json")
        self.assertEqual(run_paths.log_path.name, "终端日志.log")
        self.assertEqual(run_paths.section_dir.name, "分段报告")

        eval_paths = make_evaluation_output_paths(root, "ETH/USDT", "20260712_121000")
        self.assertEqual(
            eval_paths.state_path,
            root / "评估结果" / "20260712_121000_ETH_USDT" / "评估结果.json",
        )
        self.assertEqual(eval_paths.reflection_path.name, "反思候选.json")

        self.assertEqual(
            graph_state_log_path(root, "BTC-USD", "2026-05-19"),
            root / "图状态日志" / "BTC_USD" / "完整状态_2026-05-19.json",
        )

    def test_cli_report_save_uses_chinese_file_names(self):
        final_state = {
            "market_report": "market",
            "sentiment_report": "sentiment",
            "news_report": "news",
            "fundamentals_report": "fundamentals",
            "onchain_report": "onchain",
            "investment_debate_state": {
                "bull_history": "bull",
                "bear_history": "bear",
                "judge_decision": "manager",
            },
            "trader_investment_plan": "trader",
            "risk_debate_state": {
                "aggressive_history": "aggressive",
                "conservative_history": "conservative",
                "neutral_history": "neutral",
                "judge_decision": "decision",
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            report_path = save_report_to_disk(final_state, "BTC-USD", Path(tmp))

            self.assertEqual(report_path.name, "完整报告.md")
            self.assertTrue((Path(tmp) / "1_分析师" / "链上分析.md").exists())
            self.assertTrue((Path(tmp) / "2_研究团队" / "研究经理.md").exists())
            self.assertTrue((Path(tmp) / "3_交易计划" / "交易员.md").exists())
            self.assertTrue((Path(tmp) / "4_风险管理" / "中性观点.md").exists())
            self.assertTrue((Path(tmp) / "5_组合决策" / "最终决策.md").exists())

    def test_write_run_outputs_creates_single_run_directory(self):
        final_state = {
            "company_of_interest": "BTC-USD",
            "trade_date": "2026-07-12",
            "market_report": "market",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "onchain_report": "onchain",
            "investment_debate_state": {"judge_decision": "research"},
            "trader_investment_plan": "trader",
            "risk_debate_state": {"judge_decision": "risk"},
            "investment_plan": "plan",
            "final_trade_decision": "FINAL TRANSACTION PROPOSAL: HOLD",
        }

        with tempfile.TemporaryDirectory() as tmp:
            paths = make_run_output_paths(Path(tmp), "BTC-USD", "20260712_123000")
            write_run_outputs(
                paths=paths,
                ticker="BTC-USD",
                trade_date="2026-07-12",
                final_state=final_state,
                decision="HOLD",
                config={"llm_provider": "deepseek"},
                selected_analysts=["market", "onchain"],
            )

            self.assertTrue(paths.report_path.exists())
            self.assertTrue(paths.index_path.exists())
            self.assertTrue(paths.state_path.exists())
            self.assertTrue(paths.decision_path.exists())
            self.assertTrue(paths.config_path.exists())
            self.assertTrue((paths.section_dir / "市场分析.md").exists())
            self.assertTrue((paths.section_dir / "链上分析.md").exists())

            decision = json.loads(paths.decision_path.read_text(encoding="utf-8"))
            self.assertEqual(decision["schema_version"], "1.0")
            self.assertEqual(decision["action"], "HOLD")
            self.assertIsNone(decision["confidence"])
            self.assertIn("final_trade_decision_text", decision)

            index = json.loads(paths.index_path.read_text(encoding="utf-8"))
            self.assertEqual(index["files"]["decision"], "最终决策.json")
            self.assertEqual(index["files"]["sections"]["market_report"], "分段报告/市场分析.md")
            self.assertNotIn("terminal_log", index["files"])


if __name__ == "__main__":
    unittest.main()
