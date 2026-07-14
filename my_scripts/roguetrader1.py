"""
RogueTrader演示脚本 - 简单入口示例

此脚本演示如何使用RogueTrader多代理LLM金融交易框架的基本功能。
通过创建RogueTraderGraph实例并调用propagate方法来分析金融工具。

主要流程:
1. 从.env文件加载环境变量（API密钥等）
2. 配置LLM提供商（如DeepSeek）
3. 选择要使用的分析师类型
4. 创建RogueTraderGraph实例
5. 调用propagate方法执行分析并获取交易决策

conda activate roguetrader
uv run --frozen python my_scripts/roguetrader1.py


"""

# 极简版演示 - 使用默认配置
# 步骤：导入核心组件 → 创建图实例 → 调用propagate方法
# from pathlib import Path
# from dotenv import load_dotenv
# load_dotenv(Path(__file__).parent / ".env")
# from roguetrader.graph.trading_graph import RogueTraderGraph
# from roguetrader.default_config import DEFAULT_CONFIG
# 
# rt = RogueTraderGraph(debug=True, config=DEFAULT_CONFIG.copy())
# _, decision = rt.propert("SPY", "2026-04-04")  # 注意: 方法名应为propagate
# print(decision)


# 完整版演示 - 自定义配置
# 此脚本使用DeepSeek API进行金融分析
import datetime
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from roguetrader.graph.trading_graph import RogueTraderGraph
from roguetrader.default_config import DEFAULT_CONFIG

# 创建配置副本并自定义设置
config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "deepseek"  # 指定使用DeepSeek作为LLM提供商
config["backend_url"] = "https://api.deepseek.com"  # DeepSeek API地址
config["deep_think_llm"] = "deepseek-v4-pro"  # 深度思考模型（用于复杂推理）
config["quick_think_llm"] = "deepseek-v4-flash"  # 快速思考模型（用于简单任务）
config["results_dir"] = str(PROJECT_ROOT / "my_results")  # 固定写入项目根目录的统一结果目录
config["output_language"] = "Chinese"  # 设置输出语言为中文
config["max_debate_rounds"] = 2  # 设置辩论轮数
config["max_recur_limit"] = 50  # 增加递归限制以支持更长的分析流程

# 选择分析师 - 默认运行完整多智能体分析链路
# 可选分析师: market(市场), social(社交), news(新闻),
#            fundamentals(基本面), onchain(链上/加密货币)
selected_analysts = ["market", "social", "news", "fundamentals", "onchain"]

# 创建RogueTrader图实例
# debug=True: 启用调试模式
# config: 自定义配置
# selected_analysts: 选择的分析师列表
rt = RogueTraderGraph(debug=True, config=config, selected_analysts=selected_analysts)

# 执行分析传播
# 参数1: ticker - 要分析的金融工具代码
# 参数2: analysis_date - 分析日期
# 返回: (最终状态, 交易决策)
_, decision = rt.propagate("BTC-USD", datetime.date.today().isoformat())  # 分析比特币（使用当日日期）
# _, decision = rt.propagate("GC=F", "2026-04-04")   # 分析黄金期货

# 打印最终交易决策
print(decision)
if rt.current_output_paths:
    print(f"本次运行结果目录: {rt.current_output_paths.root}")
