"""Command-line entrypoint for the RogueTrader control panel."""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
from pathlib import Path
import signal
from typing import TextIO

from dotenv import load_dotenv

from roguetrader.control_panel.scheduler import ProjectScheduler
from roguetrader.control_panel.health_monitor import (
    DailyHealthMonitor,
    DailyHealthStateStore,
)
from roguetrader.control_panel.server import ControlPanelServer, LOOPBACK_HOSTS
from roguetrader.control_panel.storage import ConfigStore, RunHistoryStore
from roguetrader.publisher.service import LocalPublisher, PublisherWatcher
from roguetrader.publisher.sinks import CsvDecisionSink, LocalMessageSink
from roguetrader.publisher.state import PublicationState
from roguetrader.publisher.feishu import (
    FeishuNotificationManager,
    FeishuSettingsStore,
    FeishuWebhookSink,
)
from roguetrader.publisher.feishu_sheet import (
    FeishuSheetManager,
    FeishuSheetSettingsStore,
    FeishuSheetSink,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_DIR = PROJECT_ROOT / ".runtime" / "control-panel"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def publisher_state_path(state_dir: Path) -> Path:
    """Keep publisher state beside the selected runtime's control-panel state."""
    return state_dir.parent / "publisher" / "publisher.sqlite3"


def acquire_instance_lock(path: Path) -> TextIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    os.chmod(path, 0o600)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise SystemExit(f"控制面板已在运行（锁文件：{path}）。") from None
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def handle_sigterm(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RogueTrader 本地任务控制面板")
    parser.add_argument("--host", default="127.0.0.1", choices=sorted(LOOPBACK_HOSTS))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("端口必须在 1-65535 之间。")
    state_dir = args.state_dir.resolve()
    load_dotenv(PROJECT_ROOT / ".env")
    instance_lock = acquire_instance_lock(state_dir / "instance.lock")
    config_store = ConfigStore(state_dir / "config.json")
    history_store = RunHistoryStore(state_dir / "runs.json")
    scheduler = ProjectScheduler(
        config_store=config_store,
        history_store=history_store,
        project_root=PROJECT_ROOT,
        state_dir=state_dir,
    )
    summary_dir = PROJECT_ROOT / "my_results" / "汇总"
    publication_state = PublicationState(publisher_state_path(state_dir))
    feishu_manager = FeishuNotificationManager(
        FeishuSettingsStore(state_dir / "feishu-notification.json")
    )
    feishu_sheet_manager = FeishuSheetManager(
        FeishuSheetSettingsStore(state_dir / "feishu-sheet.json")
    )
    publisher = LocalPublisher(
        state=publication_state,
        sinks=(
            CsvDecisionSink(summary_dir / "每日决策.csv"),
            LocalMessageSink(summary_dir / "消息"),
            FeishuWebhookSink(feishu_manager, PROJECT_ROOT / "my_results"),
            FeishuSheetSink(feishu_sheet_manager),
        ),
    )
    publisher_watcher = PublisherWatcher(
        publisher=publisher,
        results_root=PROJECT_ROOT / "my_results",
    )
    health_monitor = DailyHealthMonitor(
        config_store=config_store,
        history_store=history_store,
        scheduler=scheduler,
        publisher_watcher=publisher_watcher,
        publication_state=publication_state,
        feishu_manager=feishu_manager,
        feishu_sheet_manager=feishu_sheet_manager,
        results_root=PROJECT_ROOT / "my_results",
        state_store=DailyHealthStateStore(state_dir / "daily-health.json"),
    )
    server = ControlPanelServer(
        (args.host, args.port),
        config_store=config_store,
        history_store=history_store,
        scheduler=scheduler,
        static_dir=STATIC_DIR,
        publisher_watcher=publisher_watcher,
        feishu_manager=feishu_manager,
        feishu_sheet_manager=feishu_sheet_manager,
        health_monitor=health_monitor,
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    scheduler.start()
    publisher_watcher.start()
    health_monitor.start()
    host, port = server.server_address[:2]
    print(f"RogueTrader 控制面板：http://{host}:{port}")
    print(f"配置目录：{state_dir}")
    print("按 Ctrl+C 停止面板；已启动的分析任务不会被强制终止。")
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n正在停止控制面板……")
    finally:
        server.shutdown()
        server.server_close()
        health_monitor.stop()
        publisher_watcher.stop()
        scheduler.stop()
        signal.signal(signal.SIGTERM, previous_sigterm_handler)
        instance_lock.close()


if __name__ == "__main__":
    main()
