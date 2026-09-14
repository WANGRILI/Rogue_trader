"""Feishu custom-bot notifications with secret-safe runtime configuration."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid

from roguetrader.publisher.models import DecisionRecord, PreparedMessage, render_message
from roguetrader.publisher.execution_plan_message import (
    load_execution_plan,
    merge_execution_and_decision_message,
    render_execution_plan_message,
)
from roguetrader.publisher.state import PublicationState


FEISHU_WEBHOOK_ENV = "ROGUETRADER_FEISHU_WEBHOOK_URL"
FEISHU_SECRET_ENV = "ROGUETRADER_FEISHU_SIGNING_SECRET"
FEISHU_HOST = "open.feishu.cn"
FEISHU_HOOK_PREFIX = "/open-apis/bot/v2/hook/"
MAX_RESPONSE_BYTES = 64 * 1024


class FeishuError(RuntimeError):
    """Base class for safe-to-display Feishu errors."""


class FeishuConfigurationError(FeishuError):
    """Raised when the local Feishu configuration is absent or invalid."""


class FeishuDeliveryError(FeishuError):
    """Raised when Feishu does not accept a notification."""


@dataclass(frozen=True)
class FeishuCredentials:
    webhook_url: str
    signing_secret: str

    @property
    def fingerprint(self) -> str:
        value = f"{self.webhook_url}\0{self.signing_secret}".encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    @classmethod
    def from_environment(cls) -> "FeishuCredentials":
        webhook_url = os.getenv(FEISHU_WEBHOOK_ENV, "").strip()
        signing_secret = os.getenv(FEISHU_SECRET_ENV, "").strip()
        if not webhook_url and not signing_secret:
            raise FeishuConfigurationError("飞书群机器人尚未配置。")
        if not webhook_url or not signing_secret:
            raise FeishuConfigurationError("飞书 Webhook 和加签密钥必须同时配置。")
        parsed = urlparse(webhook_url)
        try:
            port = parsed.port
        except ValueError:
            raise FeishuConfigurationError("飞书 Webhook 地址格式无效。") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname != FEISHU_HOST
            or port not in (None, 443)
            or not parsed.path.startswith(FEISHU_HOOK_PREFIX)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise FeishuConfigurationError("飞书 Webhook 地址格式无效。")
        hook_id = parsed.path.removeprefix(FEISHU_HOOK_PREFIX)
        if not hook_id or "/" in hook_id:
            raise FeishuConfigurationError("飞书 Webhook 地址格式无效。")
        return cls(webhook_url=webhook_url, signing_secret=signing_secret)


def credential_status() -> tuple[FeishuCredentials | None, str | None]:
    try:
        return FeishuCredentials.from_environment(), None
    except FeishuConfigurationError as exc:
        return None, str(exc)


def feishu_signature(timestamp: int, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _escape_markdown(value: Any) -> str:
    return (
        str(value or "—")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def render_feishu_card(message: PreparedMessage) -> dict[str, Any]:
    template = {
        "positive": "green",
        "negative": "red",
        "neutral": "grey",
        "info": "blue",
    }.get(message.level, "blue")
    fields = [
        {
            "is_short": True,
            "text": {
                "tag": "lark_md",
                "content": f"**{_escape_markdown(label)}**\n{_escape_markdown(value)}",
            },
        }
        for label, value in message.fields
    ]
    content_sections = message.sections or (
        (message.section_title, message.text),
    )
    content_elements: list[dict[str, Any]] = []
    for position, (title, section_text) in enumerate(content_sections):
        if position:
            content_elements.append({"tag": "hr"})
        content_elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**{_escape_markdown(title)}**\n"
                        f"{_escape_markdown(section_text)}"
                    ),
                },
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": message.title},
        },
        "elements": [
            {"tag": "div", "fields": fields},
            {"tag": "hr"},
            *content_elements,
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": (
                            f"run: {message.run_id} · event: {message.event_id[:12]}"
                        ),
                    }
                ],
            },
        ],
    }


class FeishuWebhookClient:
    def __init__(
        self,
        credentials: FeishuCredentials,
        *,
        timeout: float = 10.0,
        opener: Callable[..., Any] = urlopen,
        clock: Callable[[], datetime] | None = None,
    ):
        self.credentials = credentials
        self.timeout = timeout
        self.opener = opener
        self.clock = clock or (lambda: datetime.now().astimezone())

    def send(self, message: PreparedMessage) -> None:
        timestamp = int(self.clock().timestamp())
        payload = {
            "timestamp": str(timestamp),
            "sign": feishu_signature(timestamp, self.credentials.signing_secret),
            "msg_type": "interactive",
            "card": render_feishu_card(message),
        }
        request = Request(
            self.credentials.webhook_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise FeishuDeliveryError(f"飞书返回 HTTP {exc.code}。") from None
        except (URLError, TimeoutError, OSError):
            raise FeishuDeliveryError("无法连接飞书，请稍后重试。") from None
        if status < 200 or status >= 300:
            raise FeishuDeliveryError(f"飞书返回 HTTP {status}。")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise FeishuDeliveryError("飞书响应内容异常。")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise FeishuDeliveryError("飞书响应不是有效 JSON。") from None
        if not isinstance(result, dict):
            raise FeishuDeliveryError("飞书响应格式无效。")
        code = result.get("code", result.get("StatusCode"))
        if code not in (0, "0"):
            safe_code = str(code)[:40] if code is not None else "unknown"
            raise FeishuDeliveryError(f"飞书拒绝消息（错误码 {safe_code}）。")


@dataclass(frozen=True)
class FeishuNotificationSettings:
    enabled: bool = False
    initialized: bool = False
    tested_fingerprint: str | None = None
    last_test_at: str | None = None

    @classmethod
    def from_dict(cls, value: Any) -> "FeishuNotificationSettings":
        if not isinstance(value, dict) or value.get("schema_version", 1) != 1:
            raise FeishuConfigurationError("飞书通知运行时配置无效。")
        enabled = value.get("enabled", False)
        initialized = value.get("initialized", False)
        if not isinstance(enabled, bool) or not isinstance(initialized, bool):
            raise FeishuConfigurationError("飞书通知运行时配置无效。")
        fingerprint = value.get("tested_fingerprint")
        last_test_at = value.get("last_test_at")
        if fingerprint is not None and not isinstance(fingerprint, str):
            raise FeishuConfigurationError("飞书通知运行时配置无效。")
        if last_test_at is not None and not isinstance(last_test_at, str):
            raise FeishuConfigurationError("飞书通知运行时配置无效。")
        return cls(enabled, initialized, fingerprint, last_test_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "enabled": self.enabled,
            "initialized": self.initialized,
            "tested_fingerprint": self.tested_fingerprint,
            "last_test_at": self.last_test_at,
        }


class FeishuSettingsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.RLock()

    def load(self) -> FeishuNotificationSettings:
        with self._lock:
            if not self.path.exists():
                settings = FeishuNotificationSettings()
                self._write(settings)
                return settings
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise FeishuConfigurationError(
                    "无法读取飞书通知运行时配置。"
                ) from exc
            return FeishuNotificationSettings.from_dict(value)

    def save(self, settings: FeishuNotificationSettings) -> None:
        with self._lock:
            self._write(FeishuNotificationSettings.from_dict(settings.to_dict()))

    def _write(self, settings: FeishuNotificationSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(settings.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()


class FeishuNotificationManager:
    def __init__(
        self,
        store: FeishuSettingsStore,
        *,
        opener: Callable[..., Any] = urlopen,
        clock: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.opener = opener
        self.clock = clock or (lambda: datetime.now().astimezone())

    def is_enabled(self) -> bool:
        return self.store.load().enabled

    def credentials(self) -> FeishuCredentials:
        return FeishuCredentials.from_environment()

    def is_ready(self) -> bool:
        credentials, _ = credential_status()
        if credentials is None:
            return False
        return self.store.load().tested_fingerprint == credentials.fingerprint

    def needs_initial_baseline(self) -> bool:
        return not self.store.load().initialized

    def send_test(self) -> str:
        credentials = self.credentials()
        timestamp = self.clock().isoformat(timespec="seconds")
        message = PreparedMessage(
            event_id=f"test-{uuid.uuid4().hex}",
            title="RogueTrader 飞书推送测试",
            level="info",
            text="开发环境连接测试成功。此消息不包含任何分析结果。",
            fields=(("环境", "development"), ("测试时间", timestamp)),
            run_id="configuration-test",
            created_at=timestamp,
        )
        FeishuWebhookClient(
            credentials, opener=self.opener, clock=self.clock
        ).send(message)
        settings = self.store.load()
        self.store.save(
            FeishuNotificationSettings(
                enabled=(
                    False
                    if settings.tested_fingerprint != credentials.fingerprint
                    else settings.enabled
                ),
                initialized=settings.initialized,
                tested_fingerprint=credentials.fingerprint,
                last_test_at=timestamp,
            )
        )
        return timestamp

    def send_operational_alert(
        self,
        *,
        alert_id: str,
        trade_date: str,
        scheduled_for: str,
        checked_at: str,
        issues: tuple[dict[str, Any], ...],
        checks_completed: int,
    ) -> str:
        """Send one secret-safe daily health alert to the configured group."""
        if not self.is_enabled():
            raise FeishuConfigurationError("飞书群通知未开启。")
        if not self.is_ready():
            raise FeishuConfigurationError("飞书群通知配置尚未通过测试。")
        lines = []
        for issue in issues[:20]:
            symbol = str(issue.get("symbol") or "系统")
            label = str(issue.get("label") or "未知异常")
            detail = str(issue.get("detail") or "").strip()
            lines.append(f"- {symbol}：{label}" + (f"（{detail}）" if detail else ""))
        if len(issues) > 20:
            lines.append(f"- 另有 {len(issues) - 20} 项异常，请查看本机控制面板。")
        text = (
            "每日任务经过首次检查和三次复查后仍未恢复。\n"
            + "\n".join(lines)
            + "\n\n系统没有自动重跑付费分析。"
        )
        message = PreparedMessage(
            event_id=alert_id,
            title="RogueTrader 每日任务失败告警",
            level="negative",
            text=text,
            fields=(
                ("分析日期", trade_date),
                ("计划时间", scheduled_for),
                ("最终检查", checked_at),
                ("检查次数", f"{checks_completed}（首次 + 3 次复查）"),
            ),
            run_id=f"daily-health-{trade_date}",
            created_at=checked_at,
            section_title="失败摘要",
        )
        FeishuWebhookClient(
            self.credentials(), opener=self.opener, clock=self.clock
        ).send(message)
        return self.clock().isoformat(timespec="seconds")

    def set_enabled(self, enabled: bool) -> FeishuNotificationSettings:
        if not isinstance(enabled, bool):
            raise FeishuConfigurationError("飞书通知开关必须是布尔值。")
        settings = self.store.load()
        if enabled:
            credentials = self.credentials()
            if settings.tested_fingerprint != credentials.fingerprint:
                raise FeishuConfigurationError("请先成功发送飞书测试卡片。")
        updated = FeishuNotificationSettings(
            enabled=enabled,
            initialized=settings.initialized or enabled,
            tested_fingerprint=settings.tested_fingerprint,
            last_test_at=settings.last_test_at,
        )
        self.store.save(updated)
        return updated

    def status(self, state: PublicationState | None = None) -> dict[str, Any]:
        settings = self.store.load()
        credentials, configuration_error = credential_status()
        ready = bool(
            credentials
            and settings.tested_fingerprint == credentials.fingerprint
        )
        delivery = state.sink_status("feishu") if state else {}
        return {
            "configured": credentials is not None,
            "configuration_error": configuration_error,
            "enabled": settings.enabled,
            "test_required": not ready,
            "last_test_at": settings.last_test_at if ready else None,
            "last_success_at": delivery.get("last_success_at"),
            "last_error": delivery.get("last_error"),
            "pending": delivery.get("pending", 0),
            "expired": delivery.get("expired", 0),
        }


@dataclass(frozen=True)
class RetryPolicy:
    delays: tuple[int, ...] = (60, 300, 900, 3600, 10800, 21600)
    window_seconds: int = 24 * 60 * 60

    def delay_after_attempt(self, attempts: int) -> int:
        index = min(max(attempts - 1, 0), len(self.delays) - 1)
        return self.delays[index]


class FeishuWebhookSink:
    name = "feishu"
    retry_policy = RetryPolicy()

    def __init__(
        self,
        manager: FeishuNotificationManager,
        results_root: str | Path | None = None,
        *,
        automatic: bool = True,
    ):
        self.manager = manager
        self.results_root = (
            Path(results_root).expanduser().resolve() if results_root else None
        )
        self.automatic = automatic

    def is_enabled(self) -> bool:
        return not self.automatic or self.manager.is_enabled()

    def write(self, record: DecisionRecord) -> None:
        if not self.manager.is_ready():
            raise FeishuConfigurationError("飞书凭据尚未通过测试。")
        message = render_message(record)
        if self.results_root is not None:
            plan = load_execution_plan(self.results_root, record)
            if plan is not None:
                message = merge_execution_and_decision_message(
                    render_execution_plan_message(plan, record), message
                )
        FeishuWebhookClient(
            self.manager.credentials(),
            opener=self.manager.opener,
            clock=self.manager.clock,
        ).send(message)
