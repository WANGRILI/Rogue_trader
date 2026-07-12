import unittest
from collections.abc import Hashable

from roguetrader.default_config import DEFAULT_CONFIG
from roguetrader.graph.trading_graph import RogueTraderGraph
from roguetrader.llm_clients.openai_client import OpenAIClient


class GraphInitializationTests(unittest.TestCase):
    def test_openai_compatible_client_uses_hashable_timeout(self):
        client = OpenAIClient("qwen3:latest", provider="ollama")

        llm = client.get_llm()

        self.assertIsInstance(llm.request_timeout, Hashable)

    def test_openai_compatible_client_respects_custom_base_url(self):
        client = OpenAIClient(
            "qwen3:latest",
            base_url="http://example.test/v1",
            provider="ollama",
        )

        llm = client.get_llm()

        self.assertEqual(str(llm.openai_api_base), "http://example.test/v1")

    def test_graph_uses_configured_recursion_limit(self):
        config = DEFAULT_CONFIG.copy()
        config.update(
            {
                "llm_provider": "ollama",
                "quick_think_llm": "qwen3:latest",
                "deep_think_llm": "qwen3:latest",
                "backend_url": "http://localhost:11434/v1",
                "max_recur_limit": 7,
            }
        )

        graph = RogueTraderGraph(
            selected_analysts=["onchain"],
            config=config,
            debug=False,
        )

        args = graph.propagator.get_graph_args()
        self.assertEqual(args["config"]["recursion_limit"], 7)


if __name__ == "__main__":
    unittest.main()
