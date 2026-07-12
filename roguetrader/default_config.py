"""
默认配置文件

此模块定义了RogueTrader框架的所有默认配置项。
配置可以通过环境变量覆盖，也可以在初始化时传递字典覆盖。

配置分类:
1. 路径配置 - 项目目录、结果目录、缓存目录
2. LLM配置 - 提供商、模型、API端点
3. 思考配置 - 提供商特定的思考参数
4. 输出配置 - 报告语言
5. 辩论配置 - 辩论轮数、递归限制
6. 数据供应商配置 - 各类型数据的来源
"""

import os

DEFAULT_CONFIG = {
    # ==================== 路径配置 ====================
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),  # 项目根目录
    "results_dir": os.getenv("ROGUETRADER_RESULTS_DIR", "./my_results"),  # 结果保存目录
    "data_cache_dir": os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
        "dataflows/data_cache",
    ),  # 数据缓存目录

    # ==================== LLM配置 ====================
    # 从环境变量读取，支持自定义覆盖
    "llm_provider": os.getenv("ROGUETRADER_LLM_PROVIDER", "deepseek"),  # LLM提供商
    "deep_think_llm": os.getenv("ROGUETRADER_DEEP_THINK_LLM", "deepseek-reasoner"),  # 深度思考模型
    "quick_think_llm": os.getenv("ROGUETRADER_QUICK_THINK_LLM", "deepseek-chat"),  # 快速思考模型
    "backend_url": os.getenv("ROGUETRADER_BACKEND_URL", "https://api.deepseek.com"),  # API端点

    # ==================== 提供商特定思考配置 ====================
    # Google Gemini
    "google_thinking_level": None,  # "high" 启用思考, "minimal" 禁用思考
    # OpenAI
    "openai_reasoning_effort": None,  # "low", "medium", "high"
    # Anthropic Claude
    "anthropic_effort": None,  # "low", "medium", "high"

    # ==================== 输出配置 ====================
    # 分析师报告和最终决策的输出语言
    # 注意：内部代理辩论保持英文以保证推理质量
    "output_language": "English",

    # ==================== 辩论和讨论配置 ====================
    "max_debate_rounds": 1,  # 投资辩论轮数（多头vs空头）
    "max_risk_discuss_rounds": 1,  # 风险讨论轮数（三方辩论）
    "max_recur_limit": 20,  # LangGraph递归限制

    # ==================== 数据供应商配置 ====================
    # 类别级别配置（该类别下所有工具的默认值）
    "data_vendors": {
        # 核心股票API: alpha_vantage 或 yfinance
        "core_stock_apis": "yfinance",
        # 技术指标API
        "technical_indicators": "yfinance",
        # 基本面数据API
        "fundamental_data": "yfinance",
        # 新闻数据API
        "news_data": "yfinance",
        # 链上数据: coingecko, defillama, blockchain_com
        "onchain_data": "coingecko",
        # 加密指标: local（从数据源计算）
        "crypto_indicators": "local",
        # 加密情绪: coingecko, alternative_me
        "crypto_sentiment": "coingecko",
    },

    # 工具级别配置（优先级高于类别级别）
    # 可以覆盖特定工具的数据源
    "tool_vendors": {
        # 示例: "get_stock_data": "alpha_vantage"  # 覆盖类别默认值
    },
}
