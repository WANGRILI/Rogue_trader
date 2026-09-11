"""Idempotent Feishu spreadsheet synchronization for decision records."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from roguetrader.publisher.feishu import RetryPolicy
from roguetrader.publisher.models import DecisionRecord
from roguetrader.publisher.sinks import serialize_cell
from roguetrader.publisher.state import PublicationState


FEISHU_APP_ID_ENV = "ROGUETRADER_FEISHU_APP_ID"
FEISHU_APP_SECRET_ENV = "ROGUETRADER_FEISHU_APP_SECRET"
FEISHU_SPREADSHEET_TOKEN_ENV = "ROGUETRADER_FEISHU_SPREADSHEET_TOKEN"
FEISHU_SHEET_ID_ENV = "ROGUETRADER_FEISHU_SHEET_ID"
FEISHU_API_ROOT = "https://open.feishu.cn/open-apis"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_CELL_CHARS = 40_000
ERROR_HINTS = {
    "91403": "目标电子表格尚未授予当前应用可编辑权限",
    "99991672": "飞书应用缺少电子表格 OpenAPI 权限",
    "1310202": "飞书工作表范围无效",
    "1310211": "飞书工作表 ID 无效",
    "1310213": "目标电子表格尚未授予当前应用文档权限",
    "1310214": "飞书电子表格 Token 无效或表格对应用不可见",
    "1310215": "飞书工作表 ID 无效",
    "1310217": "飞书表格请求频率过高",
    "1310226": "飞书表格数据超出接口限制",
    "1310235": "飞书表格暂时不可用",
}

# Human-facing columns come first. Stable identifiers remain available at the end
# for auditing and remote idempotency checks.
FEISHU_SHEET_FIELDS = (
    "trade_date",
    "ticker",
    "action",
    "confidence",
    "risk_level",
    "time_horizon",
    "entry_plan",
    "stop_loss",
    "take_profit",
    "key_reasons",
    "invalidations",
    "decision_summary",
    "generated_at",
    "run_id",
    "event_id",
    "action_source",
    "source_schema_version",
    "publication_schema_version",
)


class FeishuSheetError(RuntimeError):
    """Base class for safe-to-display spreadsheet errors."""


class FeishuSheetConfigurationError(FeishuSheetError):
    """Raised when spreadsheet configuration is absent or invalid."""


class FeishuSheetDeliveryError(FeishuSheetError):
    """Raised when Feishu does not accept a spreadsheet operation."""


def _http_error_code(error: HTTPError) -> str | None:
    try:
        raw = error.read(MAX_RESPONSE_BYTES + 1)
        result = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return None
    code = result.get("code") if isinstance(result, dict) else None
    if isinstance(code, int) or (isinstance(code, str) and code.isdigit()):
        return str(code)[:20]
    return None


def _rejection_message(code: str | None, *, http_status: int | None = None) -> str:
    if code and code in ERROR_HINTS:
        return f"{ERROR_HINTS[code]}（错误码 {code}）。"
    if http_status is not None:
        detail = f"，错误码 {code}" if code else ""
        return f"飞书表格返回 HTTP {http_status}{detail}。"
    return f"飞书表格拒绝请求（错误码 {code or 'unknown'}）。"


def _valid_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value))


def _column_name(position: int) -> str:
    if position < 1:
        raise ValueError("列序号必须为正整数。")
    name = ""
    while position:
        position, remainder = divmod(position - 1, 26)
        name = chr(65 + remainder) + name
    return name


LAST_COLUMN = _column_name(len(FEISHU_SHEET_FIELDS))


@dataclass(frozen=True)
class FeishuSheetCredentials:
    app_id: str
    app_secret: str
    spreadsheet_token: str
    sheet_id: str

    @property
    def fingerprint(self) -> str:
        value = "\0".join(
            (
                self.app_id,
                self.app_secret,
                self.spreadsheet_token,
                self.sheet_id,
            )
        ).encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    @classmethod
    def from_environment(cls) -> "FeishuSheetCredentials":
        values = {
            "app_id": os.getenv(FEISHU_APP_ID_ENV, "").strip(),
            "app_secret": os.getenv(FEISHU_APP_SECRET_ENV, "").strip(),
            "spreadsheet_token": os.getenv(FEISHU_SPREADSHEET_TOKEN_ENV, "").strip(),
            "sheet_id": os.getenv(FEISHU_SHEET_ID_ENV, "").strip(),
        }
        if not any(values.values()):
            raise FeishuSheetConfigurationError("飞书表格同步尚未配置。")
        if not all(values.values()):
            raise FeishuSheetConfigurationError("飞书表格同步的四项配置必须同时提供。")
        if not re.fullmatch(r"cli_[A-Za-z0-9_-]{2,252}", values["app_id"]):
            raise FeishuSheetConfigurationError("飞书应用 App ID 格式无效。")
        if len(values["app_secret"]) > 512:
            raise FeishuSheetConfigurationError("飞书应用 App Secret 格式无效。")
        if not _valid_identifier(values["spreadsheet_token"]):
            raise FeishuSheetConfigurationError("飞书电子表格 Token 格式无效。")
        if not _valid_identifier(values["sheet_id"]):
            raise FeishuSheetConfigurationError("飞书工作表 ID 格式无效。")
        return cls(**values)


def credential_status() -> tuple[FeishuSheetCredentials | None, str | None]:
    try:
        return FeishuSheetCredentials.from_environment(), None
    except FeishuSheetConfigurationError as exc:
        return None, str(exc)


def sheet_row(record: DecisionRecord) -> list[str]:
    raw = record.to_dict()
    row = [serialize_cell(raw.get(field)) for field in FEISHU_SHEET_FIELDS]
    if any(len(cell) > MAX_CELL_CHARS for cell in row):
        raise FeishuSheetDeliveryError("决策记录包含超过飞书建议上限的单元格。")
    return row


class FeishuSheetClient:
    def __init__(
        self,
        credentials: FeishuSheetCredentials,
        *,
        timeout: float = 10.0,
        opener: Callable[..., Any] = urlopen,
        clock: Callable[[], datetime] | None = None,
    ):
        self.credentials = credentials
        self.timeout = timeout
        self.opener = opener
        self.clock = clock or (lambda: datetime.now().astimezone())
        self._tenant_token: str | None = None
        self._tenant_token_expires_at = 0.0
        self._schema_ready = False
        self._known_event_ids: set[str] = set()
        self._lock = threading.RLock()

    def verify(self) -> None:
        with self._lock:
            self._ensure_schema()

    def append(self, record: DecisionRecord) -> bool:
        with self._lock:
            self._ensure_schema()
            if record.event_id in self._known_event_ids:
                return False
            if self._contains_event_id(record.event_id):
                self._known_event_ids.add(record.event_id)
                return False
            result = self._sheet_request(
                "POST",
                self._spreadsheet_path(
                    "values_append?insertDataOption=INSERT_ROWS"
                ),
                {
                    "valueRange": {
                        "range": f"{self.credentials.sheet_id}!A:{LAST_COLUMN}",
                        "values": [sheet_row(record)],
                    }
                },
            )
            data = result.get("data")
            updates = data.get("updates") if isinstance(data, dict) else None
            if not isinstance(updates, dict) or updates.get("updatedRows") != 1:
                raise FeishuSheetDeliveryError("飞书表格追加响应格式无效。")
            self._known_event_ids.add(record.event_id)
            return True

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        header_range = f"{self.credentials.sheet_id}!A1:{LAST_COLUMN}1"
        rows = self._read_values(header_range)
        header = rows[0] if rows else []
        # Feishu represents blank cells in an otherwise materialized range as
        # JSON null.  Treat those exactly like empty strings so a new sheet can
        # be initialized without mistaking ``None`` for user-owned content.
        normalized = ["" if value is None else str(value) for value in header]
        while normalized and normalized[-1] == "":
            normalized.pop()
        if not normalized:
            self._sheet_request(
                "PUT",
                self._spreadsheet_path("values"),
                {
                    "valueRange": {
                        "range": header_range,
                        "values": [list(FEISHU_SHEET_FIELDS)],
                    }
                },
            )
        elif normalized != list(FEISHU_SHEET_FIELDS):
            raise FeishuSheetConfigurationError(
                "目标工作表表头与 RogueTrader 发布协议不一致。"
            )
        self._schema_ready = True

    def _contains_event_id(self, event_id: str) -> bool:
        result = self._sheet_request(
            "POST",
            (
                f"/sheets/v3/spreadsheets/{self.credentials.spreadsheet_token}"
                f"/sheets/{self.credentials.sheet_id}/find"
            ),
            {
                "find_condition": {
                    "range": self.credentials.sheet_id,
                    "match_case": False,
                    "match_entire_cell": True,
                    "search_by_regex": False,
                    "include_formulas": False,
                },
                "find": event_id,
            },
        )
        data = result.get("data")
        find_result = data.get("find_result") if isinstance(data, dict) else None
        if not isinstance(find_result, dict):
            raise FeishuSheetDeliveryError("飞书表格查重响应格式无效。")
        matched_cells = find_result.get("matched_cells", [])
        rows_count = find_result.get("rows_count", 0)
        if not isinstance(matched_cells, list) or not isinstance(rows_count, int):
            raise FeishuSheetDeliveryError("飞书表格查重响应格式无效。")
        return rows_count > 0 or bool(matched_cells)

    def _read_values(self, cell_range: str) -> list[list[Any]]:
        encoded_range = quote(cell_range, safe="")
        result = self._sheet_request(
            "GET", self._spreadsheet_path(f"values/{encoded_range}")
        )
        data = result.get("data")
        value_range = data.get("valueRange") if isinstance(data, dict) else None
        if not isinstance(value_range, dict):
            raise FeishuSheetDeliveryError("飞书表格返回的数据格式无效。")
        values = value_range.get("values", [])
        if not isinstance(values, list) or any(not isinstance(row, list) for row in values):
            raise FeishuSheetDeliveryError("飞书表格返回的数据格式无效。")
        return values

    def _spreadsheet_path(self, suffix: str) -> str:
        return (
            f"/sheets/v2/spreadsheets/{self.credentials.spreadsheet_token}/{suffix}"
        )

    def _sheet_request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._request_json(
            method,
            path,
            body,
            authorization=f"Bearer {self._get_tenant_token()}",
        )

    def _get_tenant_token(self) -> str:
        now = self.clock().timestamp()
        if self._tenant_token and now < self._tenant_token_expires_at:
            return self._tenant_token
        result = self._request_json(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            {
                "app_id": self.credentials.app_id,
                "app_secret": self.credentials.app_secret,
            },
        )
        token = result.get("tenant_access_token")
        expire = result.get("expire")
        if not isinstance(token, str) or not token or not isinstance(expire, int) or expire <= 0:
            raise FeishuSheetDeliveryError("飞书访问凭证响应格式无效。")
        self._tenant_token = token
        self._tenant_token_expires_at = now + max(1, expire - 300)
        return token

    def _request_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        authorization: str | None = None,
    ) -> dict[str, Any]:
        data = (
            json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None
            else None
        )
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if authorization:
            headers["Authorization"] = authorization
        request = Request(
            FEISHU_API_ROOT + path,
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            business_code = _http_error_code(exc)
            raise FeishuSheetDeliveryError(
                _rejection_message(business_code, http_status=exc.code)
            ) from None
        except (URLError, TimeoutError, OSError):
            raise FeishuSheetDeliveryError("无法连接飞书表格，请稍后重试。") from None
        if status < 200 or status >= 300:
            raise FeishuSheetDeliveryError(f"飞书表格返回 HTTP {status}。")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise FeishuSheetDeliveryError("飞书表格响应内容异常。")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise FeishuSheetDeliveryError("飞书表格响应不是有效 JSON。") from None
        if not isinstance(result, dict):
            raise FeishuSheetDeliveryError("飞书表格响应格式无效。")
        code = result.get("code")
        if code not in (0, "0"):
            safe_code = str(code)[:20] if isinstance(code, (int, float)) else None
            raise FeishuSheetDeliveryError(_rejection_message(safe_code))
        return result


@dataclass(frozen=True)
class FeishuSheetSettings:
    enabled: bool = False
    initialized: bool = False
    tested_fingerprint: str | None = None
    last_test_at: str | None = None

    @classmethod
    def from_dict(cls, value: Any) -> "FeishuSheetSettings":
        if not isinstance(value, dict) or value.get("schema_version", 1) != 1:
            raise FeishuSheetConfigurationError("飞书表格运行时配置无效。")
        enabled = value.get("enabled", False)
        initialized = value.get("initialized", False)
        fingerprint = value.get("tested_fingerprint")
        last_test_at = value.get("last_test_at")
        if not isinstance(enabled, bool) or not isinstance(initialized, bool):
            raise FeishuSheetConfigurationError("飞书表格运行时配置无效。")
        if fingerprint is not None and not isinstance(fingerprint, str):
            raise FeishuSheetConfigurationError("飞书表格运行时配置无效。")
        if last_test_at is not None and not isinstance(last_test_at, str):
            raise FeishuSheetConfigurationError("飞书表格运行时配置无效。")
        return cls(enabled, initialized, fingerprint, last_test_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "enabled": self.enabled,
            "initialized": self.initialized,
            "tested_fingerprint": self.tested_fingerprint,
            "last_test_at": self.last_test_at,
        }


class FeishuSheetSettingsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.RLock()

    def load(self) -> FeishuSheetSettings:
        with self._lock:
            if not self.path.exists():
                settings = FeishuSheetSettings()
                self._write(settings)
                return settings
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise FeishuSheetConfigurationError(
                    "无法读取飞书表格运行时配置。"
                ) from exc
            return FeishuSheetSettings.from_dict(value)

    def save(self, settings: FeishuSheetSettings) -> None:
        with self._lock:
            self._write(FeishuSheetSettings.from_dict(settings.to_dict()))

    def _write(self, settings: FeishuSheetSettings) -> None:
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


class FeishuSheetManager:
    def __init__(
        self,
        store: FeishuSheetSettingsStore,
        *,
        opener: Callable[..., Any] = urlopen,
        clock: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.opener = opener
        self.clock = clock or (lambda: datetime.now().astimezone())
        self._client: FeishuSheetClient | None = None
        self._client_fingerprint: str | None = None
        self._lock = threading.RLock()

    def is_enabled(self) -> bool:
        return self.store.load().enabled

    def credentials(self) -> FeishuSheetCredentials:
        return FeishuSheetCredentials.from_environment()

    def is_ready(self) -> bool:
        credentials, _ = credential_status()
        if credentials is None:
            return False
        return self.store.load().tested_fingerprint == credentials.fingerprint

    def needs_initial_baseline(self) -> bool:
        return not self.store.load().initialized

    def test_connection(self) -> str:
        credentials = self.credentials()
        with self._remote_lock():
            self._client_for(credentials).verify()
        timestamp = self.clock().isoformat(timespec="seconds")
        settings = self.store.load()
        self.store.save(
            FeishuSheetSettings(
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

    def set_enabled(self, enabled: bool) -> FeishuSheetSettings:
        if not isinstance(enabled, bool):
            raise FeishuSheetConfigurationError("飞书表格同步开关必须是布尔值。")
        settings = self.store.load()
        if enabled:
            credentials = self.credentials()
            if settings.tested_fingerprint != credentials.fingerprint:
                raise FeishuSheetConfigurationError("请先成功测试飞书表格连接。")
        updated = FeishuSheetSettings(
            enabled=enabled,
            initialized=settings.initialized or enabled,
            tested_fingerprint=settings.tested_fingerprint,
            last_test_at=settings.last_test_at,
        )
        self.store.save(updated)
        return updated

    def append(self, record: DecisionRecord) -> bool:
        credentials = self.credentials()
        if self.store.load().tested_fingerprint != credentials.fingerprint:
            raise FeishuSheetConfigurationError("飞书表格配置尚未通过测试。")
        with self._remote_lock():
            return self._client_for(credentials).append(record)

    def status(self, state: PublicationState | None = None) -> dict[str, Any]:
        settings = self.store.load()
        credentials, configuration_error = credential_status()
        ready = bool(
            credentials and settings.tested_fingerprint == credentials.fingerprint
        )
        delivery = state.sink_status("feishu_sheet") if state else {}
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

    def _client_for(self, credentials: FeishuSheetCredentials) -> FeishuSheetClient:
        with self._lock:
            if (
                self._client is None
                or self._client_fingerprint != credentials.fingerprint
            ):
                self._client = FeishuSheetClient(
                    credentials,
                    opener=self.opener,
                    clock=self.clock,
                )
                self._client_fingerprint = credentials.fingerprint
            return self._client

    @contextmanager
    def _remote_lock(self):
        lock_path = self.store.path.with_suffix(".sync.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(lock_path.parent, 0o700)
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield


class FeishuSheetSink:
    name = "feishu_sheet"
    retry_policy = RetryPolicy()

    def __init__(self, manager: FeishuSheetManager, *, automatic: bool = True):
        self.manager = manager
        self.automatic = automatic

    def is_enabled(self) -> bool:
        return not self.automatic or self.manager.is_enabled()

    def write(self, record: DecisionRecord) -> None:
        self.manager.append(record)
