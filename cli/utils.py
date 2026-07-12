"""
CLI用户交互工具函数

此模块包含所有用于CLI用户交互的函数，包括:
- Ticker代码输入和验证
- 日期输入
- 分析师选择
- 研究深度选择
- LLM提供商和模型选择
- 输出语言选择

依赖:
- questionary: 交互式CLI选择库
- rich: 终端美化输出
"""

import questionary
from typing import List, Optional, Tuple, Dict

from rich.console import Console

from cli.models import AnalystType
from roguetrader.llm_clients.model_catalog import get_model_options

console = Console()

# Ticker输入示例
TICKER_INPUT_EXAMPLES = "Examples: SPY, CNC.TO, 7203.T, 0700.HK"

# 分析师选项列表（显示名称 -> 枚举值）
ANALYST_ORDER = [
    ("Market Analyst", AnalystType.MARKET),
    ("Social Media Analyst", AnalystType.SOCIAL),
    ("News Analyst", AnalystType.NEWS),
    ("Fundamentals Analyst", AnalystType.FUNDAMENTALS),
    ("On-Chain Analyst", AnalystType.ONCHAIN),
]


def get_ticker() -> str:
    """
    提示用户输入ticker代码

    使用交互式文本输入，支持验证确保非空。
    返回标准化后的ticker符号（大写，保留交易所后缀）。

    返回:
        str: 标准化后的ticker代码
    """
    ticker = questionary.text(
        f"Enter the exact ticker symbol to analyze ({TICKER_INPUT_EXAMPLES}):",
        validate=lambda x: len(x.strip()) > 0 or "Please enter a valid ticker symbol.",
        style=questionary.Style(
            [
                ("text", "fg:green"),
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()

    if not ticker:
        console.print("\n[red]No ticker symbol provided. Exiting...[/red]")
        exit(1)

    return normalize_ticker_symbol(ticker)


def normalize_ticker_symbol(ticker: str) -> str:
    """
    标准化ticker输入

    保留交易所后缀（如 .TO, .HK, .T 等）并转换为大写。

    示例:
        "spy" -> "SPY"
        "0700.hk" -> "0700.HK"
        "CNC.TO" -> "CNC.TO"
    """
    return ticker.strip().upper()


def get_analysis_date() -> str:
    """
    提示用户输入分析日期

    日期格式必须为 YYYY-MM-DD，会进行格式验证。

    返回:
        str: 验证后的日期字符串
    """
    import re
    from datetime import datetime

    def validate_date(date_str: str) -> bool:
        """验证日期格式和有效性"""
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            return False
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    date = questionary.text(
        "Enter the analysis date (YYYY-MM-DD):",
        validate=lambda x: validate_date(x.strip())
        or "Please enter a valid date in YYYY-MM-DD format.",
        style=questionary.Style(
            [
                ("text", "fg:green"),
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()

    if not date:
        console.print("\n[red]No date provided. Exiting...[/red]")
        exit(1)

    return date.strip()


def select_analysts() -> List[AnalystType]:
    """
    使用交互式复选框选择分析师

    用户可以多选分析师类型，至少需要选择一个。
    使用空格键选择/取消选择，a键全选/取消全选。

    返回:
        List[AnalystType]: 选中的分析师类型列表
    """
    choices = questionary.checkbox(
        "Select Your [Analysts Team]:",
        choices=[
            questionary.Choice(display, value=value) for display, value in ANALYST_ORDER
        ],
        instruction="\n- Press Space to select/unselect analysts\n- Press 'a' to select/unselect all\n- Press Enter when done",
        validate=lambda x: len(x) > 0 or "You must select at least one analyst.",
        style=questionary.Style(
            [
                ("checkbox-selected", "fg:green"),
                ("selected", "fg:green noinherit"),
                ("highlighted", "noinherit"),
                ("pointer", "noinherit"),
            ]
        ),
    ).ask()

    if not choices:
        console.print("\n[red]No analysts selected. Exiting...[/red]")
        exit(1)

    return choices


def select_research_depth() -> int:
    """
    选择研究深度

    研究深度决定了辩论和策略讨论的轮数:
    - Shallow: 1轮，快速研究
    - Medium: 3轮，平衡研究
    - Deep: 5轮，深入研究

    返回:
        int: 辩论轮数
    """
    # 研究深度选项
    DEPTH_OPTIONS = [
        ("Shallow - Quick research, few debate and strategy discussion rounds", 1),
        ("Medium - Middle ground, moderate debate rounds and strategy discussion", 3),
        ("Deep - Comprehensive research, in depth debate and strategy discussion", 5),
    ]

    choice = questionary.select(
        "Select Your [Research Depth]:",
        choices=[
            questionary.Choice(display, value=value) for display, value in DEPTH_OPTIONS
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:yellow noinherit"),
                ("highlighted", "fg:yellow noinherit"),
                ("pointer", "fg:yellow noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print("\n[red]No research depth selected. Exiting...[/red]")
        exit(1)

    return choice


def select_shallow_thinking_agent(provider) -> str:
    """
    选择快速思考LLM引擎

    用于简单任务和快速分析。
    根据所选提供商获取可用的快速思考模型列表。

    参数:
        provider: LLM提供商名称

    返回:
        str: 选中的模型名称
    """
    choice = questionary.select(
        "Select Your [Quick-Thinking LLM Engine]:",
        choices=[
            questionary.Choice(display, value=value)
            for display, value in get_model_options(provider, "quick")
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print(
            "\n[red]No shallow thinking llm engine selected. Exiting...[/red]"
        )
        exit(1)

    return choice


def select_deep_thinking_agent(provider) -> str:
    """
    选择深度思考LLM引擎

    用于复杂推理和深入分析。
    根据所选提供商获取可用的深度思考模型列表。

    参数:
        provider: LLM提供商名称

    返回:
        str: 选中的模型名称
    """
    choice = questionary.select(
        "Select Your [Deep-Thinking LLM Engine]:",
        choices=[
            questionary.Choice(display, value=value)
            for display, value in get_model_options(provider, "deep")
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print("\n[red]No deep thinking llm engine selected. Exiting...[/red]")
        exit(1)

    return choice


def select_llm_provider() -> tuple[str, str]:
    """
    选择LLM提供商

    显示可用的LLM提供商列表及其API端点。
    支持: OpenAI, Google, Anthropic, xAI, Openrouter, Ollama

    返回:
        tuple[str, str]: (提供商显示名称, API端点URL)
    """
    # LLM提供商选项 (显示名称 -> API端点)
    BASE_URLS = [
        ("DeepSeek", "https://api.deepseek.com"),
        ("OpenAI", "https://api.openai.com/v1"),
        ("Google", "https://generativelanguage.googleapis.com/v1"),
        ("Anthropic", "https://api.anthropic.com/"),
        ("xAI", "https://api.x.ai/v1"),
        ("Openrouter", "https://openrouter.ai/api/v1"),
        ("Ollama", "http://localhost:11434/v1"),
    ]

    choice = questionary.select(
        "Select your LLM Provider:",
        choices=[
            questionary.Choice(display, value=(display, value))
            for display, value in BASE_URLS
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print("\n[red]no OpenAI backend selected. Exiting...[/red]")
        exit(1)

    display_name, url = choice
    print(f"You selected: {display_name}\tURL: {url}")

    return display_name, url


def ask_openai_reasoning_effort() -> str:
    """
    询问OpenAI推理努力级别

    控制推理模型的思考深度和token使用量。
    仅适用于支持推理的OpenAI模型。

    返回:
        str: 努力级别 ("low", "medium", "high")
    """
    choices = [
        questionary.Choice("Medium (Default)", "medium"),
        questionary.Choice("High (More thorough)", "high"),
        questionary.Choice("Low (Faster)", "low"),
    ]
    return questionary.select(
        "Select Reasoning Effort:",
        choices=choices,
        style=questionary.Style([
            ("selected", "fg:cyan noinherit"),
            ("highlighted", "fg:cyan noinherit"),
            ("pointer", "fg:cyan noinherit"),
        ]),
    ).ask()


def ask_anthropic_effort() -> str | None:
    """
    询问Anthropic Claude努力级别

    控制Claude 4.5+和4.6模型的token使用和响应详尽程度。

    返回:
        str | None: 努力级别 ("low", "medium", "high")
    """
    return questionary.select(
        "Select Effort Level:",
        choices=[
            questionary.Choice("High (recommended)", "high"),
            questionary.Choice("Medium (balanced)", "medium"),
            questionary.Choice("Low (faster, cheaper)", "low"),
        ],
        style=questionary.Style([
            ("selected", "fg:cyan noinherit"),
            ("highlighted", "fg:cyan noinherit"),
            ("pointer", "fg:cyan noinherit"),
        ]),
    ).ask()


def ask_gemini_thinking_config() -> str | None:
    """
    询问Gemini思考配置

    配置Gemini模型的思考模式。
    客户端根据模型系列映射到相应的API参数。

    返回:
        str | None: 思考级别 ("high" 或 "minimal")
    """
    return questionary.select(
        "Select Thinking Mode:",
        choices=[
            questionary.Choice("Enable Thinking (recommended)", "high"),
            questionary.Choice("Minimal/Disable Thinking", "minimal"),
        ],
        style=questionary.Style([
            ("selected", "fg:green noinherit"),
            ("highlighted", "fg:green noinherit"),
            ("pointer", "fg:green noinherit"),
        ]),
    ).ask()


def ask_output_language() -> str:
    """
    询问报告输出语言

    支持多种语言，也可选择自定义语言。

    返回:
        str: 选中的语言名称
    """
    choice = questionary.select(
        "Select Output Language:",
        choices=[
            questionary.Choice("English (default)", "English"),
            questionary.Choice("Chinese (中文)", "Chinese"),
            questionary.Choice("Japanese (日本語)", "Japanese"),
            questionary.Choice("Korean (한국어)", "Korean"),
            questionary.Choice("Hindi (हिन्दी)", "Hindi"),
            questionary.Choice("Spanish (Español)", "Spanish"),
            questionary.Choice("Portuguese (Português)", "Portuguese"),
            questionary.Choice("French (Français)", "French"),
            questionary.Choice("German (Deutsch)", "German"),
            questionary.Choice("Arabic (العربية)", "Arabic"),
            questionary.Choice("Russian (Русский)", "Russian"),
            questionary.Choice("Custom language", "custom"),
        ],
        style=questionary.Style([
            ("selected", "fg:yellow noinherit"),
            ("highlighted", "fg:yellow noinherit"),
            ("pointer", "fg:yellow noinherit"),
        ]),
    ).ask()

    if choice == "custom":
        return questionary.text(
            "Enter language name (e.g. Turkish, Vietnamese, Thai, Indonesian):",
            validate=lambda x: len(x.strip()) > 0 or "Please enter a language name.",
        ).ask().strip()

    return choice
