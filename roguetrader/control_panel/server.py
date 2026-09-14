"""Loopback-only HTTP server for the RogueTrader control panel."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from roguetrader.control_panel.models import ValidationError
from roguetrader.control_panel.scheduler import ProjectScheduler
from roguetrader.control_panel.health_monitor import DailyHealthMonitor
from roguetrader.control_panel.storage import ConfigStore, RunHistoryStore
from roguetrader.publisher.service import PublisherWatcher
from roguetrader.publisher.feishu import (
    FeishuConfigurationError,
    FeishuError,
    FeishuNotificationManager,
)
from roguetrader.publisher.feishu_sheet import (
    FeishuSheetConfigurationError,
    FeishuSheetError,
    FeishuSheetManager,
)


LOGGER = logging.getLogger(__name__)
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_BODY_BYTES = 64 * 1024


class ControlPanelServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        config_store: ConfigStore,
        history_store: RunHistoryStore,
        scheduler: ProjectScheduler,
        static_dir: Path,
        publisher_watcher: PublisherWatcher | None = None,
        feishu_manager: FeishuNotificationManager | None = None,
        feishu_sheet_manager: FeishuSheetManager | None = None,
        health_monitor: DailyHealthMonitor | None = None,
    ):
        super().__init__(address, ControlPanelHandler)
        self.config_store = config_store
        self.history_store = history_store
        self.scheduler = scheduler
        self.static_dir = static_dir
        self.publisher_watcher = publisher_watcher
        self.feishu_manager = feishu_manager
        self.feishu_sheet_manager = feishu_sheet_manager
        self.health_monitor = health_monitor


class ControlPanelHandler(BaseHTTPRequestHandler):
    server: ControlPanelServer
    server_version = "RogueTraderControl/1"
    sys_version = ""

    def log_message(self, message: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.client_address[0], message % args)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; frame-ancestors 'none'",
        )

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, filename: str, content_type: str) -> None:
        path = self.server.static_dir / filename
        if not path.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "资源不存在。"})
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _host_is_safe(self) -> bool:
        host = self.headers.get("Host", "").strip()
        if host.startswith("["):
            hostname = host.split("]", 1)[0].lstrip("[")
        else:
            hostname = host.split(":", 1)[0]
        return hostname in LOOPBACK_HOSTS

    def _mutation_is_safe(self) -> bool:
        if self.headers.get("X-RogueTrader-Control") != "1":
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.scheme in {"http", "https"} and parsed.hostname in LOOPBACK_HOSTS

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValidationError("缺少 Content-Length。")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValidationError("Content-Length 无效。") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValidationError("请求内容过大。")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("请求 JSON 无效。") from exc
        if not isinstance(value, dict):
            raise ValidationError("请求 JSON 必须是对象。")
        return value

    def _snapshot(self) -> dict[str, Any]:
        publisher = self.server.publisher_watcher
        publisher_status = (
            publisher.status() if publisher else {"service_running": False}
        )
        if self.server.feishu_manager:
            publication_state = publisher.publisher.state if publisher else None
            publisher_status["feishu"] = self.server.feishu_manager.status(
                publication_state
            )
        if self.server.feishu_sheet_manager:
            publication_state = publisher.publisher.state if publisher else None
            publisher_status["feishu_sheet"] = self.server.feishu_sheet_manager.status(
                publication_state
            )
        return {
            "config": self.server.config_store.load().to_dict(),
            "scheduler": self.server.scheduler.status(),
            "recent_runs": self.server.history_store.load()[:20],
            "publisher": publisher_status,
            "health": (
                self.server.health_monitor.status()
                if self.server.health_monitor
                else {"service_running": False, "state": "unavailable"}
            ),
        }

    def _prepare_mutation(self) -> bool:
        if not self._host_is_safe() or not self._mutation_is_safe():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "控制请求被安全策略拒绝。"})
            return False
        return True

    def do_GET(self) -> None:
        if not self._host_is_safe():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "仅允许本机访问。"})
            return
        path = urlparse(self.path).path
        if path == "/api/status":
            self._send_json(HTTPStatus.OK, self._snapshot())
        elif path in {"/", "/index.html"}:
            self._send_static("index.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._send_static("app.js", "text/javascript; charset=utf-8")
        elif path == "/styles.css":
            self._send_static("styles.css", "text/css; charset=utf-8")
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "页面不存在。"})

    def do_POST(self) -> None:
        if not self._prepare_mutation():
            return
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/api/schedule":
                allowed = {"enabled", "daily_time"}
                if not body.keys() <= allowed or not body:
                    raise ValidationError("调度请求字段无效。")
                if "enabled" in body and not isinstance(body["enabled"], bool):
                    raise ValidationError("总开关必须是布尔值。")
                if "daily_time" in body and not isinstance(body["daily_time"], str):
                    raise ValidationError("执行时间必须是字符串。")
                config = self.server.config_store.set_schedule(
                    enabled=body.get("enabled"),
                    daily_time=body.get("daily_time"),
                )
            elif path == "/api/execution-plan":
                if set(body) != {"enabled"} or not isinstance(
                    body["enabled"], bool
                ):
                    raise ValidationError("执行计划开关请求无效。")
                config = self.server.config_store.set_execution_plan_enabled(
                    body["enabled"]
                )
            elif path == "/api/tickers":
                if set(body) != {"symbol"}:
                    raise ValidationError("新增标的请求字段无效。")
                config = self.server.config_store.add_ticker(body["symbol"])
            elif path == "/api/publisher/feishu/test":
                if body:
                    raise ValidationError("飞书测试请求不接受参数。")
                manager = self._require_feishu_manager()
                sent_at = manager.send_test()
                self._send_json(HTTPStatus.OK, {"sent_at": sent_at})
                return
            elif path == "/api/publisher/feishu":
                if set(body) != {"enabled"} or not isinstance(
                    body["enabled"], bool
                ):
                    raise ValidationError("飞书通知开关请求无效。")
                manager = self._require_feishu_manager()
                if body["enabled"] and manager.needs_initial_baseline():
                    watcher = self.server.publisher_watcher
                    if watcher is None:
                        raise ValidationError("结果发布服务未启动。")
                    watcher.activate_sink(
                        "feishu", lambda: manager.set_enabled(True)
                    )
                else:
                    manager.set_enabled(body["enabled"])
                    if self.server.publisher_watcher:
                        self.server.publisher_watcher.scan_now()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "feishu": manager.status(
                            self.server.publisher_watcher.publisher.state
                            if self.server.publisher_watcher
                            else None
                        )
                    },
                )
                return
            elif path == "/api/publisher/feishu/retry":
                if body:
                    raise ValidationError("飞书重试请求不接受参数。")
                self._require_feishu_manager()
                watcher = self.server.publisher_watcher
                if watcher is None:
                    raise ValidationError("结果发布服务未启动。")
                count = watcher.retry_sink_now("feishu")
                self._send_json(HTTPStatus.OK, {"queued": count})
                return
            elif path == "/api/publisher/feishu-sheet/test":
                if body:
                    raise ValidationError("飞书表格测试请求不接受参数。")
                manager = self._require_feishu_sheet_manager()
                tested_at = manager.test_connection()
                self._send_json(HTTPStatus.OK, {"tested_at": tested_at})
                return
            elif path == "/api/publisher/feishu-sheet":
                if set(body) != {"enabled"} or not isinstance(
                    body["enabled"], bool
                ):
                    raise ValidationError("飞书表格同步开关请求无效。")
                manager = self._require_feishu_sheet_manager()
                if body["enabled"] and manager.needs_initial_baseline():
                    watcher = self.server.publisher_watcher
                    if watcher is None:
                        raise ValidationError("结果发布服务未启动。")
                    watcher.activate_sink(
                        "feishu_sheet", lambda: manager.set_enabled(True)
                    )
                else:
                    manager.set_enabled(body["enabled"])
                    if self.server.publisher_watcher:
                        self.server.publisher_watcher.scan_now()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "feishu_sheet": manager.status(
                            self.server.publisher_watcher.publisher.state
                            if self.server.publisher_watcher
                            else None
                        )
                    },
                )
                return
            elif path == "/api/publisher/feishu-sheet/retry":
                if body:
                    raise ValidationError("飞书表格重试请求不接受参数。")
                self._require_feishu_sheet_manager()
                watcher = self.server.publisher_watcher
                if watcher is None:
                    raise ValidationError("结果发布服务未启动。")
                count = watcher.retry_sink_now("feishu_sheet")
                self._send_json(HTTPStatus.OK, {"queued": count})
                return
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在。"})
                return
            self.server.scheduler.notify_config_changed()
            self._send_json(HTTPStatus.OK, {"config": config.to_dict()})
        except (ValidationError, KeyError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except FeishuConfigurationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except FeishuError as exc:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        except FeishuSheetConfigurationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except FeishuSheetError as exc:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})

    def _require_feishu_manager(self) -> FeishuNotificationManager:
        manager = self.server.feishu_manager
        if manager is None:
            raise ValidationError("飞书通知服务未启动。")
        return manager

    def _require_feishu_sheet_manager(self) -> FeishuSheetManager:
        manager = self.server.feishu_sheet_manager
        if manager is None:
            raise ValidationError("飞书表格同步服务未启动。")
        return manager

    def do_PATCH(self) -> None:
        if not self._prepare_mutation():
            return
        path = urlparse(self.path).path
        prefix = "/api/tickers/"
        if not path.startswith(prefix):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在。"})
            return
        try:
            body = self._read_json()
            if set(body) != {"enabled"}:
                raise ValidationError("标的开关请求字段无效。")
            config = self.server.config_store.set_ticker_enabled(
                unquote(path[len(prefix) :]), body["enabled"]
            )
            self.server.scheduler.notify_config_changed()
            self._send_json(HTTPStatus.OK, {"config": config.to_dict()})
        except ValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_DELETE(self) -> None:
        if not self._prepare_mutation():
            return
        path = urlparse(self.path).path
        prefix = "/api/tickers/"
        if not path.startswith(prefix):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在。"})
            return
        try:
            config = self.server.config_store.remove_ticker(unquote(path[len(prefix) :]))
            self.server.scheduler.notify_config_changed()
            self._send_json(HTTPStatus.OK, {"config": config.to_dict()})
        except ValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
