import unittest

from roguetrader.graph.signal_processing import SignalProcessor


class FailingLLM:
    def invoke(self, messages):
        raise AssertionError("LLM should not be called for explicit decisions")


class FallbackLLM:
    def invoke(self, messages):
        class Response:
            content = "SELL"

        return Response()


class SignalProcessingTests(unittest.TestCase):
    def test_extracts_explicit_decision_without_llm(self):
        processor = SignalProcessor(FailingLLM())

        self.assertEqual(
            processor.process_signal("FINAL DECISION: **UNDERWEIGHT**"),
            "UNDERWEIGHT",
        )
        self.assertEqual(
            processor.process_signal("Portfolio recommendation: overweight."),
            "OVERWEIGHT",
        )
        self.assertEqual(
            processor.process_signal(
                "Possible labels include BUY, HOLD, SELL.\nFINAL DECISION: **SELL**"
            ),
            "SELL",
        )
        self.assertEqual(processor.process_signal(""), "HOLD")

    def test_falls_back_to_llm_when_no_decision_is_present(self):
        processor = SignalProcessor(FallbackLLM())

        self.assertEqual(processor.process_signal("No final signal"), "SELL")


if __name__ == "__main__":
    unittest.main()
