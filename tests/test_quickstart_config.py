import unittest
from pathlib import Path


class QuickstartConfigTests(unittest.TestCase):
    def test_main_does_not_mix_deepseek_provider_with_openai_model(self):
        main_py = Path("main.py").read_text(encoding="utf-8")

        self.assertNotIn('config["deep_think_llm"] = "gpt-4o-mini"', main_py)
        self.assertNotIn('config["quick_think_llm"] = "gpt-4o-mini"', main_py)


if __name__ == "__main__":
    unittest.main()
