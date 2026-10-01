"""Z.ai (智谱 GLM Coding Plan) 用量采集.

通过官方用量监控接口读取个人编程套餐的 5 小时窗口 Token 限额：
GET https://open.bigmodel.cn/api/monitor/usage/quota/limit
认证头为 ``Authorization: <API Key>``（官方脚本不加 Bearer 前缀）。
API Key 只保存在本机运行目录，不进入设备快照或日志。
"""
from __future__ import annotations

import http.client
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

POLL_INTERVAL_SECONDS = 120.0
MAX_BACKOFF_SECONDS = 600.0
REQUEST_TIMEOUT_SECONDS = 10.0
IDLE_WAIT_SECONDS = 5.0
MAX_STALE_SECONDS = 1800.0
QUOTA_PATH = "/api/monitor/usage/quota/limit"
MAX_RESPONSE_BYTES = 65536

PLATFORM_HOSTS = {"cn": "open.bigmodel.cn", "intl": "api.z.ai"}

RESET_TIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)


def _text(value: Any, maximum: int, field: str) -> str:
    text = str(value or "").strip()
    if len(text.encode("utf-8")) > maximum:
        raise ValueError(f"{field} 太长")
    return text


def _parse_reset_time(raw: Any) -> datetime | None:
    """解析窗口重置时间；无法识别时返回 None（界面显示 ``--``）。"""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return _datetime_from_epoch(float(raw))
    value = str(raw).strip()
    if not value:
        return None
    if value.isdigit():
        return _datetime_from_epoch(float(value))
    if value[-1] in "Zz":
        value = value[:-1] + "+00:00"
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        for pattern in RESET_TIME_FORMATS:
            try:
                parsed = datetime.strptime(value, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed


def _datetime_from_epoch(value: float) -> datetime | None:
    """接受秒或毫秒时间戳（真实接口返回毫秒）。"""
    if value <= 0:
        return None
    if value > 1e12:
        value /= 1000.0
    try:
        return datetime.fromtimestamp(value)
    except (OverflowError, OSError, ValueError):
        return None


def _reset_epoch(item: dict[str, Any]) -> float:
    """窗口项的重置时间（epoch 秒）；无效时返回 +inf 使其排在有效窗口之后。"""
    raw = item.get("nextResetTime")
    if isinstance(raw, bool) or raw is None:
        return float("inf")
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = str(raw).strip()
        if not text.isdigit():
            parsed = _parse_reset_time(text)
            return parsed.timestamp() if parsed is not None else float("inf")
        value = float(text)
    if value <= 0:
        return float("inf")
    if value > 1e12:
        value /= 1000.0
    return value


def _window_fields(item: dict[str, Any]) -> tuple[bool, int, str]:
    """单个窗口项 → (有效, 剩余百分比, 重置时间)；无效数据安全降级。"""
    try:
        used = float(item.get("percentage"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False, 0, ""
    used = max(0.0, min(100.0, used))
    reset = _parse_reset_time(item.get("nextResetTime"))
    return True, int(round(100.0 - used)), reset.strftime("%m-%d %H:%M")[:15] if reset else ""


def usage_snapshot(payload: Any, now: datetime | None = None) -> dict[str, Any]:
    """把 quota/limit 响应规范化为快照字段（纯函数，缺失数据全部安全降级）。"""
    now = now or datetime.now()
    result: dict[str, Any] = {
        "plan_level": "",
        "five_hour_available": False,
        "five_hour_remaining_percent": 0,
        "five_hour_reset_date": "",
        "weekly_available": False,
        "weekly_remaining_percent": 0,
        "weekly_reset_date": "",
        "error": "",
    }
    if not isinstance(payload, dict):
        result["error"] = "响应格式无效"
        return result
    if payload.get("success") is False:
        message = str(payload.get("msg") or "接口返回失败").strip()
        result["error"] = message[:63] or "接口返回失败"
        return result
    data = payload.get("data")
    if not isinstance(data, dict):
        result["error"] = "响应缺少用量数据"
        return result
    result["plan_level"] = str(data.get("level") or "").strip().upper()[:15]
    limits = data.get("limits")
    if isinstance(limits, list):
        entries = [item for item in limits if isinstance(item, dict)]
        token_limits = [item for item in entries if item.get("type") == "TOKENS_LIMIT"]
        if token_limits:
            windows = [token_limits[0]]
        else:
            # 真实接口使用 CREDIT_LIMIT 并以 unit/number 区分 5 小时与周窗口；
            # 重置时间最早的一项即短窗口，最晚的一项为周窗口。
            credit_limits = [item for item in entries if item.get("type") == "CREDIT_LIMIT"]
            credit_limits.sort(key=_reset_epoch)
            windows = credit_limits[:2]
        if windows:
            available, remaining, reset = _window_fields(windows[0])
            if available:
                result["five_hour_available"] = True
                result["five_hour_remaining_percent"] = remaining
                result["five_hour_reset_date"] = reset
            elif not result["error"]:
                result["error"] = "用量百分比无法解析"
        if len(windows) > 1:
            available, remaining, reset = _window_fields(windows[1])
            if available:
                result["weekly_available"] = True
                result["weekly_remaining_percent"] = remaining
                result["weekly_reset_date"] = reset
    if not result["five_hour_available"] and not result["error"]:
        result["error"] = "接口未提供 5 小时窗口数据"
    return result


class ZaiConfigStore:
    DEFAULTS = {"api_key": "", "platform": "cn"}

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        if not path.exists():
            self.write(dict(self.DEFAULTS))

    def read(self) -> dict[str, Any]:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        return {**self.DEFAULTS, **data}

    def public(self) -> dict[str, Any]:
        config = self.read()
        return {
            "platform": config["platform"],
            "has_api_key": bool(config["api_key"]),
            "configured": bool(config["api_key"]),
        }

    def host(self) -> str:
        return PLATFORM_HOSTS.get(self.read()["platform"], PLATFORM_HOSTS["cn"])

    def validate(self, candidate: dict[str, Any], preserve_secret: bool = False) -> dict[str, Any]:
        current = self.read()
        platform = candidate.get("platform", current["platform"])
        if platform not in PLATFORM_HOSTS:
            raise ValueError("platform 必须是 cn 或 intl")
        secret = candidate.get("api_key", "" if not preserve_secret else current["api_key"])
        if preserve_secret and secret == "":
            secret = current["api_key"]
        secret = _text(secret, 200, "API Key")
        if secret and any(character.isspace() for character in secret):
            raise ValueError("API Key 不能包含空白字符")
        return {"api_key": secret, "platform": platform}

    def write(self, candidate: dict[str, Any], preserve_secret: bool = False) -> dict[str, Any]:
        validated = self.validate(candidate, preserve_secret=preserve_secret)
        payload = json.dumps(validated, ensure_ascii=False, indent=2)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with self._lock:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self.path)
        return self.public()


class ZaiError(Exception):
    """单次用量请求失败；message 不包含 API Key。"""


class ZaiService:
    def __init__(self, config: ZaiConfigStore, module_enabled: Callable[[], bool]) -> None:
        self.config_store = config
        self.module_enabled = module_enabled
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._usage: dict[str, Any] = {}
        self._last_success = 0.0
        self._last_error = ""
        self._thread = threading.Thread(target=self._run, name="zai-poller", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=3)

    def reconfigure(self) -> None:
        with self._lock:
            self._usage = {}
            self._last_success = 0.0
            self._last_error = ""
        self._wake.set()

    def set_enabled(self, enabled: bool) -> None:
        """Toggle hook: clear state and re-evaluate on the next poll."""
        self.reconfigure()

    def _enabled(self) -> bool:
        try:
            return bool(self.module_enabled())
        except Exception:
            return False

    def _request(self, api_key: str, host: str) -> dict[str, Any]:
        """Single GET against the quota endpoint; raises ZaiError on failure."""
        connection = http.client.HTTPSConnection(host, timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            connection.request("GET", QUOTA_PATH, headers={
                "Authorization": api_key,
                "Accept": "application/json",
                "Accept-Language": "en-US,en",
            })
            response = connection.getresponse()
            body = response.read(MAX_RESPONSE_BYTES + 1)
            status = response.status
        finally:
            connection.close()
        if status in (401, 403):
            raise ZaiError("API Key 认证失败，请检查 Z.ai API Key")
        if status != 200:
            raise ZaiError(f"用量接口返回 HTTP {status}")
        if len(body) > MAX_RESPONSE_BYTES:
            raise ZaiError("用量响应过大")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ZaiError("用量响应不是有效 JSON") from error
        return usage_snapshot(payload)

    def _fetch(self) -> dict[str, Any]:
        config = self.config_store.read()
        api_key = str(config.get("api_key") or "")
        if not api_key:
            raise ZaiError("尚未配置 Zai API Key")
        host = PLATFORM_HOSTS.get(config.get("platform"), PLATFORM_HOSTS["cn"])
        try:
            return self._request(api_key, host)
        except ZaiError:
            raise
        except (OSError, http.client.HTTPException) as error:
            raise ZaiError(f"无法连接 {host}") from error

    def probe(self) -> dict[str, Any]:
        """手动只读检测：一次请求，分项报告网络、认证与解析状态。"""
        checked_at = int(time.time())
        result: dict[str, Any] = {
            "ok": False,
            "checked_at_epoch": checked_at,
            "network": {"ok": False, "detail": "尚未检测"},
            "auth": {"ok": False, "detail": "尚未检测"},
            "parse": {"ok": False, "detail": "尚未检测"},
            "plan_level": "--",
            "five_hour_available": False,
            "five_hour_remaining_percent": 0,
            "five_hour_reset_date": "",
            "weekly_available": False,
            "weekly_remaining_percent": 0,
            "weekly_reset_date": "",
            "detail": "尚未完成检测",
        }
        config = self.config_store.read()
        api_key = str(config.get("api_key") or "")
        if not api_key:
            result["network"] = {"ok": False, "detail": "尚未配置 API Key"}
            result["detail"] = "请先保存 Z.ai API Key"
            return result
        host = PLATFORM_HOSTS.get(config.get("platform"), PLATFORM_HOSTS["cn"])
        connection = http.client.HTTPSConnection(host, timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            connection.request("GET", QUOTA_PATH, headers={
                "Authorization": api_key,
                "Accept": "application/json",
                "Accept-Language": "en-US,en",
            })
            response = connection.getresponse()
            body = response.read(MAX_RESPONSE_BYTES + 1)
            status = response.status
        except (OSError, http.client.HTTPException) as error:
            result["network"] = {"ok": False, "detail": f"无法连接 {host}"}
            result["detail"] = f"网络连接失败：{error.__class__.__name__}"
            return result
        finally:
            connection.close()
        result["network"] = {"ok": True, "detail": f"HTTP {status}"}
        if status in (401, 403):
            result["auth"] = {"ok": False, "detail": f"HTTP {status}"}
            result["detail"] = "API Key 认证失败，请检查 Key 是否正确"
            return result
        result["auth"] = {"ok": True, "detail": "认证通过"}
        if status != 200:
            result["parse"] = {"ok": False, "detail": f"HTTP {status}"}
            result["detail"] = f"用量接口返回 HTTP {status}"
            return result
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            result["parse"] = {"ok": False, "detail": "响应不是有效 JSON"}
            result["detail"] = "用量响应无法解析"
            return result
        usage = usage_snapshot(payload)
        result["plan_level"] = usage["plan_level"] or "--"
        result["five_hour_available"] = usage["five_hour_available"]
        result["five_hour_remaining_percent"] = usage["five_hour_remaining_percent"]
        result["five_hour_reset_date"] = usage["five_hour_reset_date"]
        result["weekly_available"] = usage["weekly_available"]
        result["weekly_remaining_percent"] = usage["weekly_remaining_percent"]
        result["weekly_reset_date"] = usage["weekly_reset_date"]
        if usage["error"]:
            result["parse"] = {"ok": False, "detail": usage["error"][:63]}
            result["detail"] = usage["error"][:63]
            return result
        result["parse"] = {"ok": True, "detail": "解析成功"}
        result["ok"] = True
        result["detail"] = "Z.ai 用量读取正常"
        return result

    def _run(self) -> None:
        backoff = POLL_INTERVAL_SECONDS
        while not self._stop.is_set():
            config = self.config_store.public()
            if not self._enabled() or not config["configured"]:
                with self._lock:
                    self._usage = {}
                    self._last_success = 0.0
                    self._last_error = ""
                backoff = POLL_INTERVAL_SECONDS
                self._wake.wait(IDLE_WAIT_SECONDS)
                self._wake.clear()
                continue
            try:
                usage = self._fetch()
                with self._lock:
                    self._usage = usage
                    self._last_error = usage.get("error", "")
                    if not self._last_error:
                        self._last_success = time.time()
                backoff = POLL_INTERVAL_SECONDS
            except ZaiError as error:
                with self._lock:
                    self._last_error = str(error)[:63]
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            self._stop.wait(backoff)
            self._wake.clear()

    def snapshot(self) -> dict[str, Any]:
        config = self.config_store.public()
        enabled = self._enabled()
        with self._lock:
            usage = dict(self._usage)
            last_success = self._last_success
            error = self._last_error
        stale = not last_success or time.time() - last_success > MAX_STALE_SECONDS
        has_data = bool(usage.get("five_hour_available"))
        connected = enabled and config["configured"] and has_data and not stale
        service_state = (
            "disabled" if not enabled
            else "needs_configuration" if not config["configured"]
            else "online" if connected
            else "error" if error
            else "connecting"
        )
        return {
            "module_enabled": enabled,
            "configuration_complete": config["configured"],
            "service_state": service_state,
            "configured": config["configured"],
            "connected": connected,
            "source": "zai_api",
            "plan_level": usage.get("plan_level", ""),
            "five_hour_available": bool(usage.get("five_hour_available", False)),
            "five_hour_remaining_percent": int(usage.get("five_hour_remaining_percent", 0)),
            "five_hour_reset_date": str(usage.get("five_hour_reset_date", ""))[:15],
            "weekly_available": bool(usage.get("weekly_available", False)),
            "weekly_remaining_percent": int(usage.get("weekly_remaining_percent", 0)),
            "weekly_reset_date": str(usage.get("weekly_reset_date", ""))[:15],
            "updated_at_epoch": int(last_success),
            "last_error": error,
        }
