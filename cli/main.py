"""
RogueTrader CLI主程序 - 交互式命令行界面

此模块提供了RogueTrader框架的交互式命令行界面，支持用户通过终端
选择分析参数、查看实时进度和最终报告。

主要功能:
1. 交互式参数选择（ticker、分析日期、语言、分析师等）
2. 实时进度显示（代理状态、消息、工具调用）
3. 统计信息追踪（LLM调用次数、token使用量等）
4. 报告生成与保存

依赖:
    - typer: CLI框架
    - rich: 终端美化输出
    - langchain: LLM调用和消息处理

使用方式:
    python -m cli
    roguetrader
"""

from typing import Optional
import datetime
import typer
from pathlib import Path
from functools import wraps
from rich.console import Console
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live
from rich.columns import Columns
from rich.markdown import Markdown
from rich.layout import Layout
from rich.text import Text
from rich.table import Table
from collections import deque
import time
from rich.tree import Tree
from rich import box
from rich.align import Align
from rich.rule import Rule

# 导入RogueTrader核心组件
from roguetrader.graph.trading_graph import RogueTraderGraph
from roguetrader.default_config import DEFAULT_CONFIG
from roguetrader.output_paths import make_run_output_paths
from roguetrader.run_outputs import write_run_outputs
from cli.models import AnalystType
from cli.utils import *
from cli.announcements import fetch_announcements, display_announcements
from cli.stats_handler import StatsCallbackHandler

console = Console()

REPORT_FILE_NAMES = {
    "market_report": "市场分析.md",
    "sentiment_report": "社交情绪.md",
    "news_report": "新闻分析.md",
    "fundamentals_report": "基本面分析.md",
    "onchain_report": "链上分析.md",
    "investment_plan": "研究决策.md",
    "trader_investment_plan": "交易计划.md",
    "final_trade_decision": "最终决策.md",
}

# 创建Typer应用实例
app = typer.Typer(
    name="RogueTrader",
    help="RogueTrader CLI: Multi-Agent LLM Crypto & Financial Trading Framework",
    add_completion=True,  # 启用shell命令补全
    no_args_is_help=True,  # 无参数时显示帮助
)


# ============================================================
# MessageBuffer类 - 消息缓冲和状态管理
# ============================================================
# MessageBuffer用于在CLI中追踪和管理代理的运行状态、消息和报告
class MessageBuffer:
    """
    消息缓冲类 - 管理CLI中的所有状态信息

    该类维护以下状态:
    - messages: 消息历史记录
    - tool_calls: 工具调用记录
    - agent_status: 各代理的执行状态 (pending/in_progress/completed/error)
    - report_sections: 各报告部分的内容
    - selected_analysts: 选中的分析师列表

    职责:
    1. 收集和存储来自LangGraph执行过程中的各种消息
    2. 追踪各代理的执行状态
    3. 管理报告的各个部分
    4. 生成用于显示的格式化数据
    """

    # 固定团队配置 - 这些代理总是运行，用户不可选择
    FIXED_AGENTS = {
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # 分析师名称映射 - 用户选择键 -> 显示名称
    ANALYST_MAPPING = {
        "market": "Market Analyst",
        "social": "Social Analyst",
        "news": "News Analyst",
        "fundamentals": "Fundamentals Analyst",
        "onchain": "On-Chain Analyst",
    }

    # 报告部分映射
    # 格式: section_name -> (analyst_key, finalizing_agent)
    # - analyst_key: 控制此部分的分析师选择键 (None表示总是包含)
    # - finalizing_agent: 完成此报告的代理名称
    REPORT_SECTIONS = {
        "market_report": ("market", "Market Analyst"),
        "sentiment_report": ("social", "Social Analyst"),
        "news_report": ("news", "News Analyst"),
        "fundamentals_report": ("fundamentals", "Fundamentals Analyst"),
        "onchain_report": ("onchain", "On-Chain Analyst"),
        "investment_plan": (None, "Research Manager"),
        "trader_investment_plan": (None, "Trader"),
        "final_trade_decision": (None, "Portfolio Manager"),
    }

    def __init__(self, max_length=100):
        self.messages = deque(maxlen=max_length)
        self.tool_calls = deque(maxlen=max_length)
        self.current_report = None
        self.final_report = None  # Store the complete final report
        self.agent_status = {}
        self.current_agent = None
        self.report_sections = {}
        self.selected_analysts = []
        self._last_message_id = None

    def init_for_analysis(self, selected_analysts):
        """
        初始化分析相关的状态

        根据用户选择的分析师类型，动态构建代理状态和报告部分。

        Args:
            selected_analysts: 分析师类型字符串列表 (如 ["market", "news"])
        """
        # 标准化分析师选择（小写）
        self.selected_analysts = [a.lower() for a in selected_analysts]

        # 动态构建代理状态字典
        self.agent_status = {}

        # 添加选中的分析师
        for analyst_key in self.selected_analysts:
            if analyst_key in self.ANALYST_MAPPING:
                self.agent_status[self.ANALYST_MAPPING[analyst_key]] = "pending"

        # 添加固定团队的代理
        for team_agents in self.FIXED_AGENTS.values():
            for agent in team_agents:
                self.agent_status[agent] = "pending"

        # 动态构建报告部分
        # 只包含用户选择的分析师对应的报告
        self.report_sections = {}
        for section, (analyst_key, _) in self.REPORT_SECTIONS.items():
            if analyst_key is None or analyst_key in self.selected_analysts:
                self.report_sections[section] = None

        # 重置其他状态
        self.current_report = None
        self.final_report = None
        self.current_agent = None
        self.messages.clear()
        self.tool_calls.clear()
        self._last_message_id = None

    def get_completed_reports_count(self):
        """
        计算已完成的报告数量

        报告被视为完成的条件:
        1. 报告部分有内容（非None）
        2. 负责完成该报告的代理状态为 "completed"

        这可以防止中间更新（如辩论轮次）被计为完成。
        """
        count = 0
        for section in self.report_sections:
            if section not in self.REPORT_SECTIONS:
                continue
            _, finalizing_agent = self.REPORT_SECTIONS[section]
            # 报告完成的两个条件：有内容 AND 负责完成的代理已完成
            has_content = self.report_sections.get(section) is not None
            agent_done = self.agent_status.get(finalizing_agent) == "completed"
            if has_content and agent_done:
                count += 1
        return count

    def add_message(self, message_type, content):
        """添加一条消息到历史记录"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.messages.append((timestamp, message_type, content))

    def add_tool_call(self, tool_name, args):
        """添加一次工具调用到历史记录"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.tool_calls.append((timestamp, tool_name, args))

    def update_agent_status(self, agent, status):
        """更新指定代理的状态"""
        if agent in self.agent_status:
            self.agent_status[agent] = status
            self.current_agent = agent

    def update_report_section(self, section_name, content):
        """更新报告部分的内容"""
        if section_name in self.report_sections:
            self.report_sections[section_name] = content
            self._update_current_report()

    def _update_current_report(self):
        """
        更新当前显示的报告

        对于面板显示，只显示最近更新的报告部分。
        """
        latest_section = None
        latest_content = None

        # 找到最近更新的报告部分
        for section, content in self.report_sections.items():
            if content is not None:
                latest_section = section
                latest_content = content

        if latest_section and latest_content:
            # 格式化报告部分用于显示
            section_titles = {
                "market_report": "Market Analysis",
                "sentiment_report": "Social Sentiment",
                "news_report": "News Analysis",
                "fundamentals_report": "Fundamentals Analysis",
                "onchain_report": "On-Chain Analysis",
                "investment_plan": "Research Team Decision",
                "trader_investment_plan": "Trading Team Plan",
                "final_trade_decision": "Portfolio Management Decision",
            }
            self.current_report = (
                f"### {section_titles[latest_section]}\n{latest_content}"
            )

        # 更新最终完整报告
        self._update_final_report()

    def _update_final_report(self):
        """
        更新最终完整报告

        整合所有报告部分生成完整的Markdown格式报告。
        报告按以下顺序组织:
        1. 分析师团队报告
        2. 研究团队决策
        3. 交易团队计划
        4. 投资组合管理决策
        """
        report_parts = []

        # 分析师团队报告
        analyst_sections = [
            "market_report",
            "sentiment_report",
            "news_report",
            "fundamentals_report",
            "onchain_report",
        ]
        if any(self.report_sections.get(section) for section in analyst_sections):
            report_parts.append("## Analyst Team Reports")
            if self.report_sections.get("market_report"):
                report_parts.append(
                    f"### Market Analysis\n{self.report_sections['market_report']}"
                )
            if self.report_sections.get("sentiment_report"):
                report_parts.append(
                    f"### Social Sentiment\n{self.report_sections['sentiment_report']}"
                )
            if self.report_sections.get("news_report"):
                report_parts.append(
                    f"### News Analysis\n{self.report_sections['news_report']}"
                )
            if self.report_sections.get("fundamentals_report"):
                report_parts.append(
                    f"### Fundamentals Analysis\n{self.report_sections['fundamentals_report']}"
                )
            if self.report_sections.get("onchain_report"):
                report_parts.append(
                    f"### On-Chain Analysis\n{self.report_sections['onchain_report']}"
                )

        # 研究团队报告
        if self.report_sections.get("investment_plan"):
            report_parts.append("## Research Team Decision")
            report_parts.append(f"{self.report_sections['investment_plan']}")

        # 交易团队报告
        if self.report_sections.get("trader_investment_plan"):
            report_parts.append("## Trading Team Plan")
            report_parts.append(f"{self.report_sections['trader_investment_plan']}")

        # 投资组合管理决策
        if self.report_sections.get("final_trade_decision"):
            report_parts.append("## Portfolio Management Decision")
            report_parts.append(f"{self.report_sections['final_trade_decision']}")

        self.final_report = "\n\n".join(report_parts) if report_parts else None


message_buffer = MessageBuffer()


# ============================================================
# UI布局和显示函数
# ============================================================

def create_layout():
    """
    创建CLI界面布局

    布局结构:
    +------------------------------------------+
    |              Header (3行)                  |
    +------------------------------------------+
    |              Main (可伸缩)                 |
    |  +----------------+-------------------+  |
    |  |   Progress    |     Messages     |  |
    |  |   (进度)       |   (消息工具)      |  |
    |  +----------------+-------------------+  |
    |  +--------------------------------------+|
    |  |         Analysis (报告)              ||
    |  +--------------------------------------+|
    +------------------------------------------+
    |              Footer (3行)                |
    +------------------------------------------+
    """
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_column(
        Layout(name="upper", ratio=3), Layout(name="analysis", ratio=5)
    )
    layout["upper"].split_row(
        Layout(name="progress", ratio=2), Layout(name="messages", ratio=3)
    )
    return layout


def format_tokens(n):
    """
    格式化token数量用于显示

    将大数字格式化为更易读的形式，如 1500 -> "1.5k"
    """
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def update_display(layout, spinner_text=None, stats_handler=None, start_time=None):
    """
    更新CLI显示的各个面板

    该函数在每次LangGraph输出新的chunk时被调用，
    更新界面上的所有面板以反映最新的状态。

    参数:
        layout: Rich布局对象
        spinner_text: 可选的加载动画文本
        stats_handler: 统计回调处理器
        start_time: 分析开始时间（用于计算已用时间）
    """
    # Header面板 - 显示欢迎信息
    layout["header"].update(
        Panel(
            "[bold green]Welcome to RogueTrader CLI[/bold green]\n"
            "[dim]Multi-agent crypto and financial analysis[/dim]",
            title="Welcome to RogueTrader",
            border_style="green",
            padding=(1, 2),
            expand=True,
        )
    )

    # 进度面板 - 显示各代理状态
    # 创建进度表格
    progress_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        box=box.SIMPLE_HEAD,  # 简单边框样式
        title=None,  # 移除多余的标题
        padding=(0, 2),  # 水平内边距
        expand=True,  # 表格扩展填满空间
    )
    progress_table.add_column("Team", style="cyan", justify="center", width=20)
    progress_table.add_column("Agent", style="green", justify="center", width=20)
    progress_table.add_column("Status", style="yellow", justify="center", width=20)

    # 所有团队和代理的映射
    all_teams = {
        "Analyst Team": [
            "Market Analyst",
            "Social Analyst",
            "News Analyst",
            "Fundamentals Analyst",
            "On-Chain Analyst",
        ],
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # 过滤团队，只显示在agent_status中存在的代理
    teams = {}
    for team, agents in all_teams.items():
        active_agents = [a for a in agents if a in message_buffer.agent_status]
        if active_agents:
            teams[team] = active_agents

    # 填充进度表格
    for team, agents in teams.items():
        # 添加第一个代理（带团队名）
        first_agent = agents[0]
        status = message_buffer.agent_status.get(first_agent, "pending")
        if status == "in_progress":
            # 运行中的代理显示旋转动画
            spinner = Spinner(
                "dots", text="[blue]in_progress[/blue]", style="bold cyan"
            )
            status_cell = spinner
        else:
            # 根据状态选择颜色
            status_color = {
                "pending": "yellow",
                "completed": "green",
                "error": "red",
            }.get(status, "white")
            status_cell = f"[{status_color}]{status}[/{status_color}]"
        progress_table.add_row(team, first_agent, status_cell)

        # 添加团队中的其余代理
        for agent in agents[1:]:
            status = message_buffer.agent_status.get(agent, "pending")
            if status == "in_progress":
                spinner = Spinner(
                    "dots", text="[blue]in_progress[/blue]", style="bold cyan"
                )
                status_cell = spinner
            else:
                status_color = {
                    "pending": "yellow",
                    "completed": "green",
                    "error": "red",
                }.get(status, "white")
                status_cell = f"[{status_color}]{status}[/{status_color}]"
            progress_table.add_row("", agent, status_cell)

        # 在每个团队后添加分隔线
        progress_table.add_row("─" * 20, "─" * 20, "─" * 20, style="dim")

    layout["progress"].update(
        Panel(progress_table, title="Progress", border_style="cyan", padding=(1, 2))
    )

    # 消息面板 - 显示最近的消息和工具调用
    messages_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        expand=True,  # 表格扩展填满空间
        box=box.MINIMAL,  # 最小边框样式
        show_lines=True,  # 保留水平线
        padding=(0, 1),  # 列之间的内边距
    )
    messages_table.add_column("Time", style="cyan", width=8, justify="center")
    messages_table.add_column("Type", style="green", width=10, justify="center")
    messages_table.add_column(
        "Content", style="white", no_wrap=False, ratio=1
    )

    # 合并工具调用和消息
    all_messages = []

    # 添加工具调用
    for timestamp, tool_name, args in message_buffer.tool_calls:
        formatted_args = format_tool_args(args)
        all_messages.append((timestamp, "Tool", f"{tool_name}: {formatted_args}"))

    # 添加普通消息
    for timestamp, msg_type, content in message_buffer.messages:
        content_str = str(content) if content else ""
        # 截断过长的内容
        if len(content_str) > 200:
            content_str = content_str[:197] + "..."
        all_messages.append((timestamp, msg_type, content_str))

    # 按时间戳降序排序（最新的在前）
    all_messages.sort(key=lambda x: x[0], reverse=True)

    # 根据可用空间计算显示的消息数量
    max_messages = 12

    # 获取前N条消息（最新的）
    recent_messages = all_messages[:max_messages]

    # 添加消息到表格
    for timestamp, msg_type, content in recent_messages:
        wrapped_content = Text(content, overflow="fold")
        messages_table.add_row(timestamp, msg_type, wrapped_content)

    layout["messages"].update(
        Panel(
            messages_table,
            title="Messages & Tools",
            border_style="blue",
            padding=(1, 2),
        )
    )

    # 分析面板 - 显示当前报告
    if message_buffer.current_report:
        layout["analysis"].update(
            Panel(
                Markdown(message_buffer.current_report),
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        layout["analysis"].update(
            Panel(
                "[italic]Waiting for analysis report...[/italic]",
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )

    # Footer面板 - 显示统计信息
    # 代理进度
    agents_completed = sum(
        1 for status in message_buffer.agent_status.values() if status == "completed"
    )
    agents_total = len(message_buffer.agent_status)

    # 报告进度 - 基于代理完成状态
    reports_completed = message_buffer.get_completed_reports_count()
    reports_total = len(message_buffer.report_sections)

    # 构建统计信息
    stats_parts = [f"Agents: {agents_completed}/{agents_total}"]

    # 从回调处理器获取LLM和工具统计
    if stats_handler:
        stats = stats_handler.get_stats()
        stats_parts.append(f"LLM: {stats['llm_calls']}")
        stats_parts.append(f"Tools: {stats['tool_calls']}")

        # Token显示
        if stats["tokens_in"] > 0 or stats["tokens_out"] > 0:
            tokens_str = f"Tokens: {format_tokens(stats['tokens_in'])}\u2191 {format_tokens(stats['tokens_out'])}\u2193"
        else:
            tokens_str = "Tokens: --"
        stats_parts.append(tokens_str)

    stats_parts.append(f"Reports: {reports_completed}/{reports_total}")

    # 已用时间
    if start_time:
        elapsed = time.time() - start_time
        elapsed_str = f"\u23f1 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
        stats_parts.append(elapsed_str)

    stats_table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    stats_table.add_column("Stats", justify="center")
    stats_table.add_row(" | ".join(stats_parts))

    layout["footer"].update(Panel(stats_table, border_style="grey50"))


def get_user_selections():
    """
    获取用户的所有选择参数

    这是一个交互式函数，逐步提示用户输入各种分析参数:
    1. Ticker代码 - 要分析的金融工具
    2. 分析日期 - 执行的日期
    3. 输出语言 - 报告的语言
    4. 分析师选择 - 启用哪些分析师
    5. 研究深度 - 辩论轮数
    6. LLM提供商 - 选择AI服务提供商
    7. 思考模型 - 深度和浅层思考模型
    8. 提供商特定配置 - 特定模型的思考参数

    返回:
        dict: 包含所有用户选择的字典
    """
    # 显示ASCII艺术欢迎信息
    with open(Path(__file__).parent / "static" / "welcome.txt", "r") as f:
        welcome_ascii = f.read()

    # 创建欢迎框内容
    welcome_content = f"{welcome_ascii}\n"
    welcome_content += "[bold green]RogueTrader: Multi-Agent LLM Crypto & Financial Trading Framework - CLI[/bold green]\n\n"
    welcome_content += "[bold]Workflow Steps:[/bold]\n"
    welcome_content += "I. Analyst Team → II. Research Team → III. Trader → IV. Risk Management → V. Portfolio Management\n\n"
    welcome_content += (
        "[dim]Multi-agent crypto and financial analysis[/dim]"
    )

    # 创建并居中欢迎框
    welcome_box = Panel(
        welcome_content,
        border_style="green",
        padding=(1, 2),
        title="Welcome to RogueTrader",
        subtitle="Multi-Agent LLM Crypto & Financial Trading Framework",
    )
    console.print(Align.center(welcome_box))
    console.print()
    console.print()

    # 获取并显示公告（失败时静默处理）
    announcements = fetch_announcements()
    display_announcements(console, announcements)

    # 创建问答框的辅助函数
    def create_question_box(title, prompt, default=None):
        box_content = f"[bold]{title}[/bold]\n"
        box_content += f"[dim]{prompt}[/dim]"
        if default:
            box_content += f"\n[dim]Default: {default}[/dim]"
        return Panel(box_content, border_style="blue", padding=(1, 2))

    # 第1步: Ticker代码
    console.print(
        create_question_box(
            "Step 1: Ticker Symbol",
            "Enter the exact ticker symbol to analyze, including exchange suffix when needed (examples: SPY, CNC.TO, 7203.T, 0700.HK)",
            "SPY",
        )
    )
    selected_ticker = get_ticker()

    # 第2步: 分析日期
    default_date = datetime.datetime.now().strftime("%Y-%m-%d")
    console.print(
        create_question_box(
            "Step 2: Analysis Date",
            "Enter the analysis date (YYYY-MM-DD)",
            default_date,
        )
    )
    analysis_date = get_analysis_date()

    # 第3步: 输出语言
    console.print(
        create_question_box(
            "Step 3: Output Language",
            "Select the language for analyst reports and final decision"
        )
    )
    output_language = ask_output_language()

    # 第4步: 选择分析师
    console.print(
        create_question_box(
            "Step 4: Analysts Team", "Select your LLM analyst agents for the analysis"
        )
    )
    selected_analysts = select_analysts()
    console.print(
        f"[green]Selected analysts:[/green] {', '.join(analyst.value for analyst in selected_analysts)}"
    )

    # 第5步: 研究深度
    console.print(
        create_question_box(
            "Step 5: Research Depth", "Select your research depth level"
        )
    )
    selected_research_depth = select_research_depth()

    # 第6步: LLM提供商
    console.print(
        create_question_box(
            "Step 6: LLM Provider", "Select your LLM provider"
        )
    )
    selected_llm_provider, backend_url = select_llm_provider()

    # 第7步: 思考模型
    console.print(
        create_question_box(
            "Step 7: Thinking Agents", "Select your thinking agents for analysis"
        )
    )
    selected_shallow_thinker = select_shallow_thinking_agent(selected_llm_provider)
    selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

    # 第8步: 提供商特定的思考配置
    thinking_level = None
    reasoning_effort = None
    anthropic_effort = None

    provider_lower = selected_llm_provider.lower()
    if provider_lower == "google":
        # Google Gemini 思考模式配置
        console.print(
            create_question_box(
                "Step 8: Thinking Mode",
                "Configure Gemini thinking mode"
            )
        )
        thinking_level = ask_gemini_thinking_config()
    elif provider_lower == "openai":
        # OpenAI 推理努力配置
        console.print(
            create_question_box(
                "Step 8: Reasoning Effort",
                "Configure OpenAI reasoning effort level"
            )
        )
        reasoning_effort = ask_openai_reasoning_effort()
    elif provider_lower == "anthropic":
        # Anthropic Claude 努力级别配置
        console.print(
            create_question_box(
                "Step 8: Effort Level",
                "Configure Claude effort level"
            )
        )
        anthropic_effort = ask_anthropic_effort()

    return {
        "ticker": selected_ticker,
        "analysis_date": analysis_date,
        "analysts": selected_analysts,
        "research_depth": selected_research_depth,
        "llm_provider": selected_llm_provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": selected_shallow_thinker,
        "deep_thinker": selected_deep_thinker,
        "google_thinking_level": thinking_level,
        "openai_reasoning_effort": reasoning_effort,
        "anthropic_effort": anthropic_effort,
        "output_language": output_language,
    }


def get_ticker():
    """获取用户输入的ticker代码"""
    return typer.prompt("", default="SPY")


def get_analysis_date():
    """
    获取用户输入的分析日期

    验证日期格式（YYYY-MM-DD）并确保日期不是未来日期
    """
    while True:
        date_str = typer.prompt(
            "", default=datetime.datetime.now().strftime("%Y-%m-%d")
        )
        try:
            # 验证日期格式并确保不是未来日期
            analysis_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            if analysis_date.date() > datetime.datetime.now().date():
                console.print("[red]Error: Analysis date cannot be in the future[/red]")
                continue
            return date_str
        except ValueError:
            console.print(
                "[red]Error: Invalid date format. Please use YYYY-MM-DD[/red]"
            )


# ============================================================
# 报告保存和显示函数
# ============================================================

def save_report_to_disk(final_state, ticker: str, save_path: Path):
    """
    将完整的分析报告保存到磁盘

    将报告按组织结构保存到子文件夹中:
    save_path/
    ├── 1_分析师/
    │   ├── 市场分析.md
    │   ├── 社交情绪.md
    │   ├── 新闻分析.md
    │   ├── 基本面分析.md
    │   └── 链上分析.md
    ├── 2_研究团队/
    │   ├── 多头研究.md
    │   ├── 空头研究.md
    │   └── 研究经理.md
    ├── 3_交易计划/
    │   └── 交易员.md
    ├── 4_风险管理/
    │   ├── 激进观点.md
    │   ├── 保守观点.md
    │   └── 中性观点.md
    ├── 5_组合决策/
    │   └── 最终决策.md
    └── 完整报告.md

    参数:
        final_state: LangGraph执行后的最终状态
        ticker: 分析的ticker代码
        save_path: 保存路径

    返回:
        Path: 完整报告文件的路径
    """
    save_path.mkdir(parents=True, exist_ok=True)
    sections = []

    # 1. 分析师报告
    analysts_dir = save_path / "1_分析师"
    analyst_parts = []
    if final_state.get("market_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "市场分析.md").write_text(final_state["market_report"], encoding="utf-8")
        analyst_parts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "社交情绪.md").write_text(final_state["sentiment_report"], encoding="utf-8")
        analyst_parts.append(("Social Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "新闻分析.md").write_text(final_state["news_report"], encoding="utf-8")
        analyst_parts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "基本面分析.md").write_text(final_state["fundamentals_report"], encoding="utf-8")
        analyst_parts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if final_state.get("onchain_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "链上分析.md").write_text(final_state["onchain_report"], encoding="utf-8")
        analyst_parts.append(("On-Chain Analyst", final_state["onchain_report"]))
    if analyst_parts:
        content = "\n\n".join(f"### {name}\n{text}" for name, text in analyst_parts)
        sections.append(f"## I. Analyst Team Reports\n\n{content}")

    # 2. 研究团队报告
    if final_state.get("investment_debate_state"):
        research_dir = save_path / "2_研究团队"
        debate = final_state["investment_debate_state"]
        research_parts = []
        if debate.get("bull_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "多头研究.md").write_text(debate["bull_history"], encoding="utf-8")
            research_parts.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "空头研究.md").write_text(debate["bear_history"], encoding="utf-8")
            research_parts.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "研究经理.md").write_text(debate["judge_decision"], encoding="utf-8")
            research_parts.append(("Research Manager", debate["judge_decision"]))
        if research_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in research_parts)
            sections.append(f"## II. Research Team Decision\n\n{content}")

    # 3. 交易团队报告
    if final_state.get("trader_investment_plan"):
        trading_dir = save_path / "3_交易计划"
        trading_dir.mkdir(exist_ok=True)
        (trading_dir / "交易员.md").write_text(final_state["trader_investment_plan"], encoding="utf-8")
        sections.append(f"## III. Trading Team Plan\n\n### Trader\n{final_state['trader_investment_plan']}")

    # 4. 风险管理团队报告
    if final_state.get("risk_debate_state"):
        risk_dir = save_path / "4_风险管理"
        risk = final_state["risk_debate_state"]
        risk_parts = []
        if risk.get("aggressive_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "激进观点.md").write_text(risk["aggressive_history"], encoding="utf-8")
            risk_parts.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "保守观点.md").write_text(risk["conservative_history"], encoding="utf-8")
            risk_parts.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "中性观点.md").write_text(risk["neutral_history"], encoding="utf-8")
            risk_parts.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in risk_parts)
            sections.append(f"## IV. Risk Management Team Decision\n\n{content}")

        # 5. 投资组合经理决策
        if risk.get("judge_decision"):
            portfolio_dir = save_path / "5_组合决策"
            portfolio_dir.mkdir(exist_ok=True)
            (portfolio_dir / "最终决策.md").write_text(risk["judge_decision"], encoding="utf-8")
            sections.append(f"## V. Portfolio Manager Decision\n\n### Portfolio Manager\n{risk['judge_decision']}")

    # 写入整合报告
    header = f"# Trading Analysis Report: {ticker}\n\nGenerated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    complete_report = save_path / "完整报告.md"
    complete_report.write_text(header + "\n\n".join(sections), encoding="utf-8")
    return complete_report


def display_complete_report(final_state):
    """
    在终端显示完整的分析报告

    按顺序显示各个团队的分析结果，避免内容被截断。
    显示顺序:
    I.   分析师团队报告
    II.  研究团队决策
    III. 交易团队计划
    IV.  风险管理团队决策
    V.   投资组合经理决策
    """
    console.print()
    console.print(Rule("Complete Analysis Report", style="bold green"))

    # I. 分析师团队报告
    analysts = []
    if final_state.get("market_report"):
        analysts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts.append(("Social Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if final_state.get("onchain_report"):
        analysts.append(("On-Chain Analyst", final_state["onchain_report"]))
    if analysts:
        console.print(Panel("[bold]I. Analyst Team Reports[/bold]", border_style="cyan"))
        for title, content in analysts:
            console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

    # II. 研究团队报告
    if final_state.get("investment_debate_state"):
        debate = final_state["investment_debate_state"]
        research = []
        if debate.get("bull_history"):
            research.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research.append(("Research Manager", debate["judge_decision"]))
        if research:
            console.print(Panel("[bold]II. Research Team Decision[/bold]", border_style="magenta"))
            for title, content in research:
                console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

    # III. 交易团队
    if final_state.get("trader_investment_plan"):
        console.print(Panel("[bold]III. Trading Team Plan[/bold]", border_style="yellow"))
        console.print(Panel(Markdown(final_state["trader_investment_plan"]), title="Trader", border_style="blue", padding=(1, 2)))

    # IV. 风险管理团队
    if final_state.get("risk_debate_state"):
        risk = final_state["risk_debate_state"]
        risk_reports = []
        if risk.get("aggressive_history"):
            risk_reports.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_reports.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_reports.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_reports:
            console.print(Panel("[bold]IV. Risk Management Team Decision[/bold]", border_style="red"))
            for title, content in risk_reports:
                console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

        # V. 投资组合经理决策
        if risk.get("judge_decision"):
            console.print(Panel("[bold]V. Portfolio Manager Decision[/bold]", border_style="green"))
            console.print(Panel(Markdown(risk["judge_decision"]), title="Portfolio Manager", border_style="blue", padding=(1, 2)))


def update_research_team_status(status):
    """更新研究团队成员的状态（不包括Trader）"""
    research_team = ["Bull Researcher", "Bear Researcher", "Research Manager"]
    for agent in research_team:
        message_buffer.update_agent_status(agent, status)


# ============================================================
# 代理状态更新辅助函数
# ============================================================

# 分析师执行顺序
ANALYST_ORDER = ["market", "social", "news", "fundamentals", "onchain"]
# 分析师名称映射
ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
    "onchain": "On-Chain Analyst",
}
# 分析师报告映射
ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
    "onchain": "onchain_report",
}


def update_analyst_statuses(message_buffer, chunk):
    """
    根据累积的报告状态更新分析师状态

    状态更新逻辑:
    - 有报告的分析师 -> completed
    - 第一个没有报告的分析师 -> in_progress
    - 其余没有报告的分析师 -> pending
    - 当所有分析师完成时，设置Bull Researcher为in_progress
    """
    selected = message_buffer.selected_analysts
    found_active = False

    for analyst_key in ANALYST_ORDER:
        if analyst_key not in selected:
            continue

        agent_name = ANALYST_AGENT_NAMES[analyst_key]
        report_key = ANALYST_REPORT_MAP[analyst_key]

        # 从当前chunk捕获新的报告内容
        if chunk.get(report_key):
            message_buffer.update_report_section(report_key, chunk[report_key])

        # 从累积的部分检查状态（不只是当前chunk）
        has_report = bool(message_buffer.report_sections.get(report_key))

        if has_report:
            message_buffer.update_agent_status(agent_name, "completed")
        elif not found_active:
            message_buffer.update_agent_status(agent_name, "in_progress")
            found_active = True
        else:
            message_buffer.update_agent_status(agent_name, "pending")

    # 当所有分析师完成时，转换研究团队到in_progress
    if not found_active and selected:
        if message_buffer.agent_status.get("Bull Researcher") == "pending":
            message_buffer.update_agent_status("Bull Researcher", "in_progress")


# ============================================================
# 消息分类和格式化函数
# ============================================================

def extract_content_string(content):
    """
    从各种消息格式中提取字符串内容

    支持的消息格式:
    - str: 直接返回
    - dict: 提取 'text' 字段
    - list: 合并所有文本项

    返回:
        str: 提取的文本内容，如果为空则返回None
    """
    import ast

    def is_empty(val):
        """使用Python的真值判断值是否为空"""
        if val is None or val == '':
            return True
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return True
            try:
                return not bool(ast.literal_eval(s))
            except (ValueError, SyntaxError):
                return False  # 无法解析 = 真实文本
        return not bool(val)

    if is_empty(content):
        return None

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):
        text = content.get('text', '')
        return text.strip() if not is_empty(text) else None

    if isinstance(content, list):
        text_parts = [
            item.get('text', '').strip() if isinstance(item, dict) and item.get('type') == 'text'
            else (item.strip() if isinstance(item, str) else '')
            for item in content
        ]
        result = ' '.join(t for t in text_parts if t and not is_empty(t))
        return result if result else None

    return str(content).strip() if not is_empty(content) else None


def classify_message_type(message) -> tuple[str, str | None]:
    """
    将LangChain消息分类为显示类型并提取内容

    返回:
        tuple[str, str | None]: (类型, 内容)
        - 类型: User, Agent, Data, Control, System
        - 内容: 提取的字符串或None
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    content = extract_content_string(getattr(message, 'content', None))

    if isinstance(message, HumanMessage):
        if content and content.strip() == "Continue":
            return ("Control", content)
        return ("User", content)

    if isinstance(message, ToolMessage):
        return ("Data", content)

    if isinstance(message, AIMessage):
        return ("Agent", content)

    # 未知类型的后备处理
    return ("System", content)


def format_tool_args(args, max_length=80) -> str:
    """
    格式化工具参数用于终端显示

    截断过长的参数列表
    """
    result = str(args)
    if len(result) > max_length:
        return result[:max_length - 3] + "..."
    return result

def run_analysis():
    """
    运行完整的交易分析流程

    这是CLI的主要执行函数，流程如下:
    1. 获取用户选择参数
    2. 创建配置
    3. 初始化统计处理器
    4. 创建并初始化LangGraph
    5. 创建结果目录
    6. 设置日志装饰器
    7. 启动实时显示界面
    8. 执行LangGraph分析流
    9. 处理最终结果并保存报告
    """
    # 第1步: 获取用户选择参数
    selections = get_user_selections()

    # 第2步: 使用用户选择创建配置
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = selections["research_depth"]
    config["max_risk_discuss_rounds"] = selections["research_depth"]
    config["quick_think_llm"] = selections["shallow_thinker"]
    config["deep_think_llm"] = selections["deep_thinker"]
    config["backend_url"] = selections["backend_url"]
    config["llm_provider"] = selections["llm_provider"].lower()
    # 提供商特定的思考配置
    config["google_thinking_level"] = selections.get("google_thinking_level")
    config["openai_reasoning_effort"] = selections.get("openai_reasoning_effort")
    config["anthropic_effort"] = selections.get("anthropic_effort")
    config["output_language"] = selections.get("output_language", "English")

    # 第3步: 创建统计回调处理器用于追踪LLM/工具调用
    stats_handler = StatsCallbackHandler()

    # 第4步: 标准化分析师选择
    selected_set = {analyst.value for analyst in selections["analysts"]}
    selected_analyst_keys = [a for a in ANALYST_ORDER if a in selected_set]

    # 第5步: 初始化图实例并绑定回调
    graph = RogueTraderGraph(
        selected_analyst_keys,
        config=config,
        debug=True,
        callbacks=[stats_handler],
    )

    # 第6步: 初始化消息缓冲区
    message_buffer.init_for_analysis(selected_analyst_keys)

    # 第7步: 记录开始时间
    start_time = time.time()

    # 第8步: 创建结果目录结构
    output_paths = make_run_output_paths(config["results_dir"], selections["ticker"])
    results_dir = output_paths.root
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir = output_paths.section_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = output_paths.log_path
    log_file.touch(exist_ok=True)

    # 第9步: 创建日志装饰器
    # 消息日志装饰器 - 将消息写入文件
    def save_message_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(message_type, content):
            func(message_type, content)
            if len(obj.messages) > 0:
                timestamp, message_type, content = obj.messages[-1]
                content = content.replace("\n", " ")
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"{timestamp} [{message_type}] {content}\n")
        return wrapper

    # 工具调用日志装饰器
    def save_tool_call_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(tool_name, args):
            func(tool_name, args)
            if len(obj.tool_calls) > 0:
                timestamp, saved_tool_name, saved_args = obj.tool_calls[-1]
                args_str = ", ".join(f"{k}={v}" for k, v in saved_args.items())
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"{timestamp} [Tool Call] {tool_name}({args_str})\n")
        return wrapper

    # 报告部分日志装饰器
    def save_report_section_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(section_name, content):
            func(section_name, content)
            if section_name in obj.report_sections and obj.report_sections[section_name] is not None:
                content = obj.report_sections[section_name]
                if content:
                    file_name = REPORT_FILE_NAMES.get(section_name, f"{section_name}.md")
                    text = "\n".join(str(item) for item in content) if isinstance(content, list) else content
                    with open(report_dir / file_name, "w", encoding="utf-8") as f:
                        f.write(text)
        return wrapper

    # 应用日志装饰器
    message_buffer.add_message = save_message_decorator(message_buffer, "add_message")
    message_buffer.add_tool_call = save_tool_call_decorator(message_buffer, "add_tool_call")
    message_buffer.update_report_section = save_report_section_decorator(message_buffer, "update_report_section")

    # 第10步: 创建界面布局
    layout = create_layout()

    # 第11步: 启动实时显示循环
    with Live(layout, refresh_per_second=4) as live:
        # 初始显示
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        # 添加初始消息
        message_buffer.add_message("System", f"Selected ticker: {selections['ticker']}")
        message_buffer.add_message(
            "System", f"Analysis date: {selections['analysis_date']}"
        )
        message_buffer.add_message(
            "System",
            f"Selected analysts: {', '.join(analyst.value for analyst in selections['analysts'])}",
        )
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        # 更新第一个分析师为in_progress
        first_analyst_key = selections["analysts"][0].value
        first_analyst = ANALYST_AGENT_NAMES.get(
            first_analyst_key,
            f"{first_analyst_key.capitalize()} Analyst",
        )
        message_buffer.update_agent_status(first_analyst, "in_progress")
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        # 创建加载动画文本
        spinner_text = (
            f"Analyzing {selections['ticker']} on {selections['analysis_date']}..."
        )
        update_display(layout, spinner_text, stats_handler=stats_handler, start_time=start_time)

        # 第12步: 初始化状态并获取图参数
        init_agent_state = graph.propagator.create_initial_state(
            selections["ticker"], selections["analysis_date"]
        )
        # 传递回调给图配置用于工具执行追踪
        args = graph.propagator.get_graph_args(callbacks=[stats_handler])

        # 第13步: 执行LangGraph流
        trace = []
        for chunk in graph.graph.stream(init_agent_state, **args):
            # 处理消息（通过消息ID跳过重复项）
            if len(chunk["messages"]) > 0:
                last_message = chunk["messages"][-1]
                msg_id = getattr(last_message, "id", None)

                if msg_id != message_buffer._last_message_id:
                    message_buffer._last_message_id = msg_id

                    # 添加消息到缓冲区
                    msg_type, content = classify_message_type(last_message)
                    if content and content.strip():
                        message_buffer.add_message(msg_type, content)

                    # 处理工具调用
                    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                        for tool_call in last_message.tool_calls:
                            if isinstance(tool_call, dict):
                                message_buffer.add_tool_call(
                                    tool_call["name"], tool_call["args"]
                                )
                            else:
                                message_buffer.add_tool_call(tool_call.name, tool_call.args)

            # 更新分析师状态（每个chunk都运行）
            update_analyst_statuses(message_buffer, chunk)

            # 研究团队 - 处理投资辩论状态
            if chunk.get("investment_debate_state"):
                debate_state = chunk["investment_debate_state"]
                bull_hist = debate_state.get("bull_history", "").strip()
                bear_hist = debate_state.get("bear_history", "").strip()
                judge = debate_state.get("judge_decision", "").strip()

                # 只有在有实际内容时更新状态
                if bull_hist or bear_hist:
                    update_research_team_status("in_progress")
                if bull_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Bull Researcher Analysis\n{bull_hist}"
                    )
                if bear_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Bear Researcher Analysis\n{bear_hist}"
                    )
                if judge:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Research Manager Decision\n{judge}"
                    )
                    update_research_team_status("completed")
                    message_buffer.update_agent_status("Trader", "in_progress")

            # 交易团队
            if chunk.get("trader_investment_plan"):
                message_buffer.update_report_section(
                    "trader_investment_plan", chunk["trader_investment_plan"]
                )
                if message_buffer.agent_status.get("Trader") != "completed":
                    message_buffer.update_agent_status("Trader", "completed")
                    message_buffer.update_agent_status("Aggressive Analyst", "in_progress")

            # 风险管理团队 - 处理风险辩论状态
            if chunk.get("risk_debate_state"):
                risk_state = chunk["risk_debate_state"]
                agg_hist = risk_state.get("aggressive_history", "").strip()
                con_hist = risk_state.get("conservative_history", "").strip()
                neu_hist = risk_state.get("neutral_history", "").strip()
                judge = risk_state.get("judge_decision", "").strip()

                if agg_hist:
                    if message_buffer.agent_status.get("Aggressive Analyst") != "completed":
                        message_buffer.update_agent_status("Aggressive Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Aggressive Analyst Analysis\n{agg_hist}"
                    )
                if con_hist:
                    if message_buffer.agent_status.get("Conservative Analyst") != "completed":
                        message_buffer.update_agent_status("Conservative Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Conservative Analyst Analysis\n{con_hist}"
                    )
                if neu_hist:
                    if message_buffer.agent_status.get("Neutral Analyst") != "completed":
                        message_buffer.update_agent_status("Neutral Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Neutral Analyst Analysis\n{neu_hist}"
                    )
                if judge:
                    if message_buffer.agent_status.get("Portfolio Manager") != "completed":
                        message_buffer.update_agent_status("Portfolio Manager", "in_progress")
                        message_buffer.update_report_section(
                            "final_trade_decision", f"### Portfolio Manager Decision\n{judge}"
                        )
                        message_buffer.update_agent_status("Aggressive Analyst", "completed")
                        message_buffer.update_agent_status("Conservative Analyst", "completed")
                        message_buffer.update_agent_status("Neutral Analyst", "completed")
                        message_buffer.update_agent_status("Portfolio Manager", "completed")

            # 更新显示
            update_display(layout, stats_handler=stats_handler, start_time=start_time)

            trace.append(chunk)

        # 第14步: 获取最终状态和决策
        final_state = trace[-1]
        decision = graph.process_signal(final_state["final_trade_decision"])
        write_run_outputs(
            paths=output_paths,
            ticker=selections["ticker"],
            trade_date=selections["analysis_date"],
            final_state=final_state,
            decision=decision,
            config=config,
            selected_analysts=selected_analyst_keys,
        )

        # 更新所有代理状态为完成
        for agent in message_buffer.agent_status:
            message_buffer.update_agent_status(agent, "completed")

        message_buffer.add_message(
            "System", f"Completed analysis for {selections['analysis_date']}"
        )

        # 更新最终报告部分
        for section in message_buffer.report_sections.keys():
            if section in final_state:
                message_buffer.update_report_section(section, final_state[section])

        update_display(layout, stats_handler=stats_handler, start_time=start_time)

    # 第15步: 分析后提示（在Live上下文外以获得干净的交互）
    console.print("\n[bold cyan]Analysis Complete![/bold cyan]\n")

    # 提示保存报告
    save_choice = typer.prompt("Save report?", default="Y").strip().upper()
    if save_choice in ("Y", "YES", ""):
        save_path_str = typer.prompt(
            "Save path (press Enter for default)",
            default=str(results_dir)
        ).strip()
        save_path = Path(save_path_str)
        try:
            report_file = save_report_to_disk(final_state, selections["ticker"], save_path)
            console.print(f"\n[green]✓ Report saved to:[/green] {save_path.resolve()}")
            console.print(f"  [dim]Complete report:[/dim] {report_file.name}")
        except Exception as e:
            console.print(f"[red]Error saving report: {e}[/red]")

    # 提示显示完整报告
    display_choice = typer.prompt("\nDisplay full report on screen?", default="Y").strip().upper()
    if display_choice in ("Y", "YES", ""):
        display_complete_report(final_state)


def main():
    """CLI主入口函数"""
    run_analysis()


@app.callback()
def callback():
    """RogueTrader CLI command group."""


@app.command()
def analyze():
    """运行交易分析的命令"""
    run_analysis()


if __name__ == "__main__":
    app()
