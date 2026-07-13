import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from roguetrader.default_config import DEFAULT_CONFIG
from roguetrader.llm_clients.agent_registry import AgentLLMRegistry


class DummyClient:
    def __init__(self, value):
        self.value = value

    def get_llm(self):
        return self.value


class AgentLLMRegistryTests(unittest.TestCase):
    def test_prompt_profile_overrides_identity_focus_and_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agents.yaml"
            path.write_text(
                """
agents:
  market_analyst:
    prompt:
      identity: Custom identity
      focus: Custom focus
      style: Custom style
""",
                encoding="utf-8",
            )
            config = DEFAULT_CONFIG.copy()
            config["agent_config_path"] = str(path)
            registry = AgentLLMRegistry(config)

            prompt = registry.render_prompt(
                "market_analyst",
                "Default identity",
                "Default focus",
                "Default style",
                "Body",
            )

            self.assertIn("Custom identity", prompt)
            self.assertIn("Custom focus", prompt)
            self.assertIn("Custom style", prompt)
            self.assertIn("Body", prompt)

    def test_missing_prompt_fields_use_builtin_defaults(self):
        config = DEFAULT_CONFIG.copy()
        config["agent_config_path"] = "/tmp/roguetrader_missing_agents.yaml"
        registry = AgentLLMRegistry(config)

        prompt = registry.render_prompt(
            "unknown_agent",
            "Default identity",
            "Default focus",
            "Default style",
            "Body",
        )

        self.assertIn("Default identity", prompt)
        self.assertIn("Default focus", prompt)
        self.assertIn("Default style", prompt)

    def test_invalid_yaml_raises_readable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agents.yaml"
            path.write_text("agents: [", encoding="utf-8")
            config = DEFAULT_CONFIG.copy()
            config["agent_config_path"] = str(path)

            with self.assertRaisesRegex(ValueError, "Invalid agent YAML config"):
                AgentLLMRegistry(config)

    def test_agent_model_override_and_cache_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agents.yaml"
            path.write_text(
                """
agents:
  market_analyst:
    llm:
      provider: ollama
      backend_url: http://localhost:11434/v1
      model: qwen3:latest
""",
                encoding="utf-8",
            )
            config = DEFAULT_CONFIG.copy()
            config["agent_config_path"] = str(path)

            created = []

            def fake_create_llm_client(**kwargs):
                created.append(kwargs)
                return DummyClient(object())

            with patch(
                "roguetrader.llm_clients.agent_registry.create_llm_client",
                side_effect=fake_create_llm_client,
            ):
                registry = AgentLLMRegistry(config)
                first = registry.get_llm("market_analyst", "quick")
                second = registry.get_llm("market_analyst", "quick")

            self.assertIs(first, second)
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0]["provider"], "ollama")
            self.assertEqual(created[0]["model"], "qwen3:latest")

    def test_runtime_config_beats_yaml_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agents.yaml"
            path.write_text(
                """
defaults:
  provider: deepseek
  quick_model: deepseek-v4-flash
""",
                encoding="utf-8",
            )
            config = DEFAULT_CONFIG.copy()
            config.update(
                {
                    "agent_config_path": str(path),
                    "llm_provider": "ollama",
                    "quick_think_llm": "qwen3:latest",
                    "backend_url": "http://localhost:11434/v1",
                }
            )

            created = []

            def fake_create_llm_client(**kwargs):
                created.append(kwargs)
                return DummyClient(object())

            with patch(
                "roguetrader.llm_clients.agent_registry.create_llm_client",
                side_effect=fake_create_llm_client,
            ):
                AgentLLMRegistry(config).get_llm("market_analyst", "quick")

            self.assertEqual(created[0]["provider"], "ollama")
            self.assertEqual(created[0]["model"], "qwen3:latest")


if __name__ == "__main__":
    unittest.main()
