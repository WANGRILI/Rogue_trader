"""CLI for local result publication."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from roguetrader.output_paths import RUN_RESULTS_DIR
from roguetrader.publisher.loader import load_completed_run
from roguetrader.publisher.models import render_message
from roguetrader.publisher.service import LocalPublisher
from roguetrader.publisher.sinks import CsvDecisionSink, LocalMessageSink
from roguetrader.publisher.state import PublicationState
from roguetrader.publisher.feishu import (
    FeishuNotificationManager,
    FeishuSettingsStore,
    FeishuWebhookSink,
)
from roguetrader.publisher.feishu_sheet import (
    FeishuSheetError,
    FeishuSheetManager,
    FeishuSheetSettingsStore,
    FeishuSheetSink,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_ROOT = PROJECT_ROOT / "my_results"
DEFAULT_CONTROL_STATE_DIR = PROJECT_ROOT / ".runtime" / "control-panel"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RogueTrader 本地结果发布器")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--message-dir", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish = subparsers.add_parser("publish", help="发布一个已完成结果")
    publish.add_argument("run_dir", type=Path)
    mode = publish.add_mutually_exclusive_group()
    mode.add_argument("--csv-only", action="store_true")
    mode.add_argument("--message-only", action="store_true")

    scan = subparsers.add_parser("scan", help="扫描并发布新增结果")
    scan.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    scan.add_argument(
        "--backfill",
        action="store_true",
        help="显式处理历史结果；默认首次扫描只建立基线",
    )

    preview = subparsers.add_parser("preview", help="预览本地消息，不写入状态")
    preview.add_argument("run_dir", type=Path)

    subparsers.add_parser("feishu-test", help="发送不含分析结果的飞书测试卡片")
    feishu_send = subparsers.add_parser(
        "feishu-send", help="显式发送一个已完成结果到飞书"
    )
    feishu_send.add_argument("run_dir", type=Path)
    feishu_send.add_argument(
        "--force",
        action="store_true",
        help="明确重发已经成功投递的结果",
    )
    subparsers.add_parser(
        "feishu-sheet-test", help="测试飞书电子表格连接并校验表头"
    )
    feishu_sheet_sync = subparsers.add_parser(
        "feishu-sheet-sync", help="显式同步一个已完成结果到飞书电子表格"
    )
    feishu_sheet_sync.add_argument("run_dir", type=Path)
    feishu_sheet_sync.add_argument(
        "--force",
        action="store_true",
        help="重新处理已经成功同步的结果；远端 event_id 仍会防止重复行",
    )
    return parser


def make_publisher(args: argparse.Namespace) -> LocalPublisher:
    results_root = results_root_for_args(args)
    project_root = results_root.parent
    summary_root = results_root / "汇总"
    state_dir = args.state_dir or project_root / ".runtime" / "publisher"
    csv_path = args.csv or summary_root / "每日决策.csv"
    message_dir = args.message_dir or summary_root / "消息"
    # CSV is always the durable publication boundary.  "message-only" means
    # no additional channel beyond the local CSV-backed message package.
    sinks = [CsvDecisionSink(csv_path)]
    if not getattr(args, "csv_only", False):
        sinks.append(LocalMessageSink(message_dir))
    return LocalPublisher(PublicationState(state_dir / "publisher.sqlite3"), sinks)


def results_root_for_args(args: argparse.Namespace) -> Path:
    if args.command == "scan":
        candidate = args.results_root.expanduser().resolve()
        return candidate.parent if candidate.name == RUN_RESULTS_DIR else candidate
    if args.command == "publish":
        run_dir = args.run_dir.expanduser().resolve()
        if run_dir.parent.name == RUN_RESULTS_DIR:
            return run_dir.parent.parent
    return DEFAULT_RESULTS_ROOT


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    args = build_parser().parse_args()
    if args.command == "preview":
        record = load_completed_run(args.run_dir)
        print(json.dumps(render_message(record).to_dict(), ensure_ascii=False, indent=2))
        return

    if args.command == "feishu-test":
        manager = FeishuNotificationManager(
            FeishuSettingsStore(DEFAULT_CONTROL_STATE_DIR / "feishu-notification.json")
        )
        sent_at = manager.send_test()
        print(json.dumps({"sent_at": sent_at, "successful": True}, ensure_ascii=False))
        return

    if args.command == "feishu-send":
        record = load_completed_run(args.run_dir)
        results_root = results_root_for_args(
            argparse.Namespace(command="publish", run_dir=args.run_dir)
        )
        state_dir = args.state_dir or results_root.parent / ".runtime" / "publisher"
        state = PublicationState(state_dir / "publisher.sqlite3")
        manager = FeishuNotificationManager(
            FeishuSettingsStore(DEFAULT_CONTROL_STATE_DIR / "feishu-notification.json")
        )
        if not manager.is_ready():
            raise SystemExit("请先成功发送飞书测试卡片。")
        if args.force:
            state.force_retry_delivery(record.event_id, "feishu")
        else:
            state.reset_delivery(record.event_id, "feishu")
        report = LocalPublisher(
            state,
            (
                CsvDecisionSink(results_root / "汇总" / "每日决策.csv"),
                FeishuWebhookSink(manager, automatic=False),
            ),
        ).publish_run(args.run_dir).to_dict()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["successful"]:
            raise SystemExit(1)
        return

    if args.command == "feishu-sheet-test":
        manager = FeishuSheetManager(
            FeishuSheetSettingsStore(
                DEFAULT_CONTROL_STATE_DIR / "feishu-sheet.json"
            )
        )
        try:
            tested_at = manager.test_connection()
        except FeishuSheetError as exc:
            raise SystemExit(str(exc)) from None
        print(json.dumps({"tested_at": tested_at, "successful": True}, ensure_ascii=False))
        return

    if args.command == "feishu-sheet-sync":
        record = load_completed_run(args.run_dir)
        results_root = results_root_for_args(
            argparse.Namespace(command="publish", run_dir=args.run_dir)
        )
        state_dir = args.state_dir or results_root.parent / ".runtime" / "publisher"
        state = PublicationState(state_dir / "publisher.sqlite3")
        manager = FeishuSheetManager(
            FeishuSheetSettingsStore(
                DEFAULT_CONTROL_STATE_DIR / "feishu-sheet.json"
            )
        )
        if not manager.is_ready():
            raise SystemExit("请先成功测试飞书电子表格连接。")
        if args.force:
            state.force_retry_delivery(record.event_id, "feishu_sheet")
        else:
            state.reset_delivery(record.event_id, "feishu_sheet")
        report = LocalPublisher(
            state,
            (
                CsvDecisionSink(results_root / "汇总" / "每日决策.csv"),
                FeishuSheetSink(manager, automatic=False),
            ),
        ).publish_run(args.run_dir).to_dict()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["successful"]:
            raise SystemExit(1)
        return

    publisher = make_publisher(args)
    if args.command == "publish":
        report = publisher.publish_run(args.run_dir).to_dict()
    else:
        report = publisher.scan(args.results_root, backfill=args.backfill)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    errors = report.get("errors", [])
    successful = report.get("successful", not errors)
    if errors or not successful:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
