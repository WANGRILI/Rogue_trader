"""
RogueTrader主入口脚本 - 简单演示

此脚本展示了使用RogueTrader框架进行金融分析的最小配置示例。
相比cli/main.py的交互式界面，此脚本直接执行固定配置的分析任务。

功能说明:
1. 使用默认配置初始化RogueTraderGraph
2. 执行单个 ticker 的分析
3. 输出最终交易决策

使用方式:
    python main.py

依赖:
    - .env 文件中配置的 API 密钥 (默认如 DEEPSEEK_API_KEY)
    - yfinance 或 alpha_vantage 数据源
"""

from roguetrader.graph.trading_graph import RogueTraderGraph
from roguetrader.default_config import DEFAULT_CONFIG

from dotenv import load_dotenv

# 从 .env 文件加载环境变量
load_dotenv()

# 创建默认配置的副本进行自定义修改
config = DEFAULT_CONFIG.copy()
# 默认配置使用 DeepSeek；如需切换 OpenAI，请同时修改 llm_provider、backend_url 和模型名。
# 设置辩论轮数（研究员之间的辩论迭代次数）
config["max_debate_rounds"] = 1

# 配置数据供应商
# 支持的数据源:
#   - core_stock_apis: 股票核心数据 (alpha_vantage, yfinance)
#   - technical_indicators: 技术指标 (alpha_vantage, yfinance)
#   - fundamental_data: 基本面数据 (alpha_vantage, yfinance)
#   - news_data: 新闻数据 (alpha_vantage, yfinance)
# 默认使用 yfinance，无需额外API密钥
config["data_vendors"] = {
    "core_stock_apis": "yfinance",
    "technical_indicators": "yfinance",
    "fundamental_data": "yfinance",
    "news_data": "yfinance",
}

# 使用自定义配置初始化图实例
# debug=True: 启用调试模式，输出更多执行信息
ta = RogueTraderGraph(debug=True, config=config)

# 执行前向传播分析
# 参数1: ticker - 金融工具代码 (如 "BTC-USD", "SPY", "AAPL")
# 参数2: analysis_date - 分析日期 (格式: YYYY-MM-DD)
# 返回: (最终状态字典, 交易决策文本)
_, decision = ta.propagate("BTC-USD", "2026-04-14")
print(decision)

# 反思和记忆功能（已注释）
# 可用于学习历史错误并改进未来决策
# ta.reflect_and_remember(1000)  # 参数: 头寸收益率(%)
