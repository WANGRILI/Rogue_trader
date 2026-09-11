"""Daily RogueTrader analysis application entrypoint.

Schedulers keep calling ``roguetrader1.py``. Versioned releases execute this
application file so the scheduler-facing compatibility shim stays unchanged.
"""

import datetime
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from roguetrader.graph.trading_graph import RogueTraderGraph  # noqa: E402
from roguetrader.default_config import DEFAULT_CONFIG  # noqa: E402


config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "deepseek"
config["backend_url"] = "https://api.deepseek.com"
config["deep_think_llm"] = "deepseek-v4-pro"
config["quick_think_llm"] = "deepseek-v4-flash"
config["results_dir"] = str(PROJECT_ROOT / "my_results")
config["output_language"] = "Chinese"
config["max_debate_rounds"] = 2
config["max_recur_limit"] = 50

selected_analysts = ["market", "social", "news", "fundamentals", "onchain"]

rt = RogueTraderGraph(debug=True, config=config, selected_analysts=selected_analysts)
_, decision = rt.propagate("BTC-USD", datetime.date.today().isoformat())

print(decision)
if rt.current_output_paths:
    print(f"本次运行结果目录: {rt.current_output_paths.root}")
