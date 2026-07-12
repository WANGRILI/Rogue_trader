import unittest

from typer.testing import CliRunner

from cli.main import (
    ANALYST_AGENT_NAMES,
    ANALYST_ORDER,
    ANALYST_REPORT_MAP,
    MessageBuffer,
    app,
)


class CliBehaviorTests(unittest.TestCase):
    def test_analyze_help_does_not_start_interactive_flow(self):
        result = CliRunner().invoke(app, ["analyze", "--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Usage: ", result.output)
        self.assertIn("analyze", result.output)
        self.assertNotIn("Step 1: Ticker Symbol", result.output)

    def test_onchain_analyst_is_wired_into_cli_state(self):
        self.assertIn("onchain", ANALYST_ORDER)
        self.assertEqual(ANALYST_AGENT_NAMES["onchain"], "On-Chain Analyst")
        self.assertEqual(ANALYST_REPORT_MAP["onchain"], "onchain_report")

        buffer = MessageBuffer()
        buffer.init_for_analysis(["onchain"])

        self.assertIn("On-Chain Analyst", buffer.agent_status)
        self.assertIn("onchain_report", buffer.report_sections)


if __name__ == "__main__":
    unittest.main()
