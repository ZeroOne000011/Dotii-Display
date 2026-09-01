#!/usr/bin/env python3
"""Read Codex account and thread state through the official app-server protocol."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from runtime_paths import application_root


DETACHED_ACTIVE_WINDOW_SECONDS = 24 * 60 * 60
TRANSIENT_REFRESH_FAILURE_LIMIT = 6


class AppServerError(RuntimeError):
    """Raised when the Codex app-server cannot satisfy a request."""


def _as_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _epoch(value: Any) -> int:
    number = _as_number(value)
    if number is not None:
        if number > 10_000_000_000:
            number /= 1000
        return max(0, int(number))
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return max(0, int(parsed.timestamp()))
        except ValueError:
            return 0
    return 0


def _text(value: Any) -> str:
    """Extract user-visible text without exposing tool arguments or output."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(filter(None, (_text(item) for item in value))).strip()
    if not isinstance(value, dict):
        return ""
    item_type = str(value.get("type", ""))
    if item_type in {"image", "localImage", "audio", "localAudio"}:
        return ""
    for key in ("text", "value"):
        if isinstance(value.get(key), str):
            return value[key].strip()
    return _text(value.get("content"))


def _summary_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(filter(None, (_summary_text(item) for item in value))).strip()
    if isinstance(value, dict):
        for key in ("text", "summaryText", "summary_text"):
            if isinstance(value.get(key), str):
                return value[key].strip()
        return _summary_text(value.get("summary"))
    return ""


def _user_message_text(value: Any) -> str:
    """Remove desktop attachment metadata while retaining the authored request."""
    text = _text(value)
    for marker in ("\n## My request:\n", "\n# My request:\n"):
        if marker in text:
            return text.rsplit(marker, 1)[1].strip()
    return text


def _safe_line(value: str, maximum: int) -> str:
    cleaned = " ".join(value.replace("\x00", "").split())
    for marker in ("**", "__", "`", "###", "##"):
        cleaned = cleaned.replace(marker, "")
    return cleaned.encode("utf-8")[:maximum].decode("utf-8", errors="ignore").strip()


def _safe_message(value: str, maximum: int | None = None) -> str:
    """Keep visible paragraph breaks, optionally applying a byte cap."""
    cleaned = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    if maximum is None:
        return cleaned
    encoded = cleaned.encode("utf-8")
    if len(encoded) <= maximum:
        return cleaned
    marker = b"..."
    if maximum <= len(marker):
        return marker[:maximum].decode("ascii")
    visible = encoded[: maximum - len(marker)].decode("utf-8", errors="ignore").rstrip()
    return visible + marker.decode("ascii")


def _resolve_codex_command(explicit: str | None, runtime_folder: Path) -> list[str]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    environment = os.environ.get("STATE_DISPLAY_CODEX_COMMAND")
    if environment:
        candidates.append(Path(environment).expanduser())

    candidates.extend([
        runtime_folder / "codex-cli" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
        application_root() / "tools" / "codex-cli" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
        Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
    ])
    bundled_node = application_root() / "tools" / "node" / "node.exe"
    node = str(bundled_node) if bundled_node.is_file() else shutil.which("node")
    for candidate in candidates:
        if candidate.is_file():
            if candidate.suffix.lower() == ".js":
                if not node:
                    raise AppServerError("找到了 Codex CLI，但没有找到 Node.js")
                return [node, str(candidate)]
            return [str(candidate)]

    executable = shutil.which("codex")
    if executable:
        return [executable]
    raise AppServerError("未找到可启动的 Codex CLI，请确认应用目录或系统回退路径中的运行时文件完整")


class AppServerClient:
    def __init__(self, command: list[str], cwd: Path, timeout: float = 15.0) -> None:
        self.command = command
        self.cwd = cwd
        self.timeout = timeout
        self.process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self.notifications: queue.Queue[dict[str, Any]] = queue.Queue()
        self._closed = threading.Event()

    def start(self) -> None:
        if self.process is not None:
            return
        try:
            self.process = subprocess.Popen(
                [*self.command, "app-server", "--listen", "stdio://"],
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            raise AppServerError(f"无法启动 Codex app-server：{error}") from error
        threading.Thread(target=self._read_stdout, name="codex-app-server", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="codex-app-server-errors", daemon=True).start()
        self.request("initialize", {
            "clientInfo": {
                "name": "state_display_bridge",
                "title": "Dotii Bridge",
                "version": "0.2.0",
            }
        })
        self.notify("initialized", {})

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            response_id = message.get("id")
            if isinstance(response_id, int):
                with self._pending_lock:
                    target = self._pending.get(response_id)
                if target is not None:
                    target.put(message)
            elif isinstance(message.get("method"), str):
                self.notifications.put(message)
        self._closed.set()

    def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        for line in self.process.stderr:
            cleaned = line.strip()
            if cleaned:
                print(f"[App Server] {cleaned}")

    def _send(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None or self.process.poll() is not None:
            raise AppServerError("Codex app-server 已退出")
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock:
            self.process.stdin.write(encoded)
            self.process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        target: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = target
        try:
            message: dict[str, Any] = {"method": method, "id": request_id}
            if params is not None:
                message["params"] = params
            self._send(message)
            try:
                response = target.get(timeout=self.timeout)
            except queue.Empty as error:
                raise AppServerError(f"App Server 请求超时：{method}") from error
            if "error" in response:
                detail = response.get("error")
                if isinstance(detail, dict):
                    detail = detail.get("message", detail)
                raise AppServerError(f"App Server 请求失败 {method}：{detail}")
            return response.get("result")
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def close(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()


def _rate_windows(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    buckets: list[dict[str, Any]] = []
    # The top-level bucket is the active Codex limit. Responses can also
    # contain a base_model_inference weekly placeholder at 0% used; keeping
    # Codex first prevents that equal-duration window from appearing as a
    # transient 100% remaining value.
    single = result.get("rateLimits")
    if isinstance(single, dict):
        buckets.append(single)
    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        codex = by_id.get("codex")
        if isinstance(codex, dict):
            buckets.append(codex)
        buckets.extend(
            value for key, value in by_id.items()
            if key != "codex" and isinstance(value, dict)
        )
    windows: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for bucket in buckets:
        for name in ("primary", "secondary"):
            window = bucket.get(name)
            if not isinstance(window, dict):
                continue
            duration = int(_as_number(window.get("windowDurationMins")) or 0)
            reset = int(_as_number(window.get("resetsAt")) or 0)
            marker = (duration, reset)
            if marker not in seen:
                seen.add(marker)
                windows.append(window)
    return windows


def account_snapshot(rate_result: Any, usage_result: Any, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    windows = _rate_windows(rate_result)
    five_hour_candidates = [
        window for window in windows
        if int(_as_number(window.get("windowDurationMins")) or 0) == 5 * 60
    ]
    five_hour = five_hour_candidates[0] if five_hour_candidates else None
    weekly_candidates = [
        window for window in windows
        if (_as_number(window.get("windowDurationMins")) or 0) >= 6 * 24 * 60
    ]
    weekly = max(weekly_candidates, key=lambda item: _as_number(item.get("windowDurationMins")) or 0) \
        if weekly_candidates else None
    account: dict[str, Any] = {
        "five_hour_available": False,
        "five_hour_remaining_percent": 0,
        "five_hour_reset_date": "",
        "weekly_available": False,
        "weekly_remaining_percent": 0,
        "weekly_used_percent": 0,
        "reset_date": "",
        "weekly_tokens_available": False,
        "weekly_tokens": 0,
        "weekly_tokens_as_of": "",
        "plan_type": "",
        "rate_limit_reached": "",
    }
    if five_hour is not None:
        used = _as_number(five_hour.get("usedPercent"))
        reset = _epoch(five_hour.get("resetsAt"))
        if used is not None:
            used = max(0, min(100, used))
            account["five_hour_available"] = True
            account["five_hour_remaining_percent"] = round(100 - used)
        if reset:
            account["five_hour_reset_date"] = datetime.fromtimestamp(reset).astimezone().strftime("%m-%d %H:%M")
    if weekly is not None:
        used = _as_number(weekly.get("usedPercent"))
        reset = _epoch(weekly.get("resetsAt"))
        if used is not None:
            used = max(0, min(100, used))
            account["weekly_available"] = True
            account["weekly_used_percent"] = round(used)
            account["weekly_remaining_percent"] = round(100 - used)
        if reset:
            account["reset_date"] = datetime.fromtimestamp(reset).astimezone().strftime("%m-%d %H:%M")

    if isinstance(rate_result, dict):
        rate = rate_result.get("rateLimits")
        if isinstance(rate, dict):
            account["plan_type"] = _safe_line(str(rate.get("planType") or ""), 31)
            account["rate_limit_reached"] = _safe_line(str(rate.get("rateLimitReachedType") or ""), 31)

    buckets = usage_result.get("dailyUsageBuckets") if isinstance(usage_result, dict) else None
    if isinstance(buckets, list):
        daily_tokens: dict[date, int] = {}
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            try:
                bucket_date = date.fromisoformat(str(bucket.get("startDate") or "")[:10])
            except ValueError:
                continue
            tokens = _as_number(bucket.get("tokens"))
            if tokens is not None and tokens >= 0:
                daily_tokens[bucket_date] = daily_tokens.get(bucket_date, 0) + int(tokens)
        if daily_tokens:
            latest_date = max(daily_tokens)
            period_start = latest_date - timedelta(days=6)
            account["weekly_tokens_available"] = True
            account["weekly_tokens"] = sum(
                tokens for bucket_date, tokens in daily_tokens.items()
                if period_start <= bucket_date <= latest_date
            )
            account["weekly_tokens_as_of"] = latest_date.strftime("%m-%d")
    return account


def _turn_usage(turn: dict[str, Any]) -> int:
    usage = turn.get("usage") or turn.get("tokenUsage")
    if not isinstance(usage, dict):
        return 0
    for key in ("totalTokens", "total_tokens"):
        value = _as_number(usage.get(key))
        if value is not None and value >= 0:
            return int(value)
    total = 0
    for key in ("inputTokens", "input_tokens", "outputTokens", "output_tokens"):
        value = _as_number(usage.get(key))
        if value is not None and value >= 0:
            total += int(value)
    return total


def _plan_counts(turn: dict[str, Any], items: list[dict[str, Any]]) -> tuple[int, int]:
    candidates: list[Any] = [turn.get("plan")]
    candidates.extend(item.get("plan") for item in items if item.get("type") == "plan")
    for candidate in reversed(candidates):
        if not isinstance(candidate, list):
            continue
        entries = [entry for entry in candidate if isinstance(entry, dict)]
        if entries:
            completed = sum(1 for entry in entries if str(entry.get("status", "")).lower() == "completed")
            return completed, len(entries)
    return 0, 0


def thread_snapshot(thread: Any, now_epoch: int | None = None) -> dict[str, Any]:
    now_epoch = now_epoch or int(time.time())
    if not isinstance(thread, dict):
        return {
            "status": "idle", "thread_id": "", "turn_id": "", "title": "暂无任务",
            "last_user_message": "", "last_assistant_message": "", "reasoning_summary": "",
            "conversation_mode": "empty", "codex_messages": [],
            "current_action": "等待新任务", "duration_seconds": 0,
            "user_message_count": 0, "message_count": 0,
            "token_count": 0, "token_count_available": False,
            "plan_completed": 0, "plan_total": 0,
            "started_at_epoch": 0, "last_updated_epoch": 0,
        }

    turns = [turn for turn in thread.get("turns", []) if isinstance(turn, dict)]
    turns.sort(key=lambda turn: _epoch(
        turn.get("startedAt") or turn.get("createdAt") or
        turn.get("updatedAt") or turn.get("completedAt")
    ))
    latest_turn = turns[-1] if turns else {}
    latest_items = [item for item in latest_turn.get("items", []) if isinstance(item, dict)]
    items = latest_items
    latest_agent_messages: list[tuple[str, str]] = []
    user_message_count = 0
    token_count = 0
    for turn in turns:
        token_count += _turn_usage(turn)
        for item in turn.get("items", []):
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "userMessage":
                user_message_count += 1
    # A desktop continuation can create a new active turn without repeating the
    # userMessage item.  Keep current status/progress tied to the latest turn,
    # while resolving the latest visible user request across the whole thread.
    user_message = ""
    for turn in reversed(turns):
        turn_items = [item for item in turn.get("items", []) if isinstance(item, dict)]
        for item in reversed(turn_items):
            if item.get("type") != "userMessage":
                continue
            value = _user_message_text(item.get("content"))
            if value:
                user_message = value
                break
        if user_message:
            break

    assistant_message = ""
    for item in items:
        item_type = item.get("type")
        if item_type == "agentMessage":
            value = _text(item.get("text") or item.get("content"))
            if value:
                phase = str(item.get("phase") or "").replace("_", "").lower()
                latest_agent_messages.append((phase, value))

    runtime = thread.get("status") if isinstance(thread.get("status"), dict) else {}
    active_flags = {str(flag).lower() for flag in runtime.get("activeFlags", []) if isinstance(flag, str)}
    turn_status = str(latest_turn.get("status", "")).replace("_", "").lower()
    last_final_index = -1
    last_visible_activity_index = -1
    for index, item in enumerate(latest_items):
        item_type = str(item.get("type") or "")
        if item_type == "userMessage":
            last_visible_activity_index = index
        elif item_type == "agentMessage":
            phase = str(item.get("phase") or "").replace("_", "").lower()
            if phase in {"final", "finalanswer"}:
                last_final_index = index
            elif phase == "commentary":
                last_visible_activity_index = index
        elif str(item.get("status") or "").replace("_", "").lower() in {
                "inprogress", "running", "pending"}:
            last_visible_activity_index = index
    # Since Codex CLI 0.147, reading a desktop-owned thread from a second
    # app-server can repair ``updatedAt`` to the current time.  ``recencyAt``
    # remains the reliable activity timestamp for that detached view.
    recency = _epoch(thread.get("recencyAt"))
    updated = recency or _epoch(thread.get("updatedAt") or latest_turn.get("updatedAt") or
                                latest_turn.get("completedAt") or latest_turn.get("startedAt"))
    recently_updating = updated > 0 and 0 <= now_epoch - updated < 15 * 60
    detached_recent = recency > 0 and 0 <= now_epoch - recency < DETACHED_ACTIVE_WINDOW_SECONDS
    completed_at = _epoch(latest_turn.get("completedAt"))
    resumed_visible_activity = (
        completed_at > 0 and
        recency > completed_at + 5 and
        last_visible_activity_index >= 0 and
        last_visible_activity_index > last_final_index
    )
    detached_active = (
        turn_status == "interrupted" and
        (not latest_items or resumed_visible_activity) and
        detached_recent and
        str(runtime.get("type", "")).lower() == "notloaded" and
        str(thread.get("source", "")).lower() in {"vscode", "appserver"}
    )
    if any("waiting" in flag or "approval" in flag for flag in active_flags):
        status = "waiting_user"
    elif turn_status in {"inprogress", "running", "active"}:
        status = "working"
    elif turn_status == "completed":
        status = "completed"
    elif turn_status == "interrupted" and (
            latest_turn.get("completedAt") is None and recently_updating or detached_active):
        # A second read-only app-server sees a desktop-owned in-flight turn as
        # interrupted. Newer versions may also expose an empty placeholder
        # turn with completedAt populated while recencyAt continues to move.
        status = "working"
    elif turn_status in {"failed", "interrupted", "cancelled", "canceled"}:
        status = "failed"
    elif str(runtime.get("type", "")).lower() == "active":
        status = "working"
    else:
        status = "idle" if not turns else "completed"

    progress_messages = [text for phase, text in latest_agent_messages if phase == "commentary"]
    final_messages = [text for phase, text in latest_agent_messages if phase in {"final", "finalanswer"}]
    if status in {"working", "waiting_user"}:
        # Match the Codex app: while a turn is active, expose only the visible
        # commentary stream. A final-answer item is not mixed into progress.
        display_messages = progress_messages
        conversation_mode = "progress"
    elif final_messages:
        # A completed turn displays one final answer and hides all commentary.
        display_messages = [final_messages[-1]]
        conversation_mode = "final"
    elif latest_agent_messages:
        # Older app-server payloads may omit phase. Use only the last visible
        # agent message as the terminal fallback; never replay the whole turn.
        display_messages = [latest_agent_messages[-1][1]]
        conversation_mode = "final"
    else:
        display_messages = []
        conversation_mode = "empty"

    safe_messages = [safe for value in display_messages if (safe := _safe_message(value))]
    if safe_messages:
        assistant_message = safe_messages[-1]

    current_action = (
        progress_messages[-1]
        if status in {"working", "waiting_user"} and progress_messages else ""
    )
    if status == "waiting_user":
        current_action = "等待用户确认"
    elif not current_action:
        for item in reversed(latest_items):
            item_type = item.get("type")
            item_status = str(item.get("status", "")).replace("_", "").lower()
            if item_status not in {"inprogress", "running", "pending"}:
                continue
            current_action = {
                "commandExecution": "正在运行命令",
                "fileChange": "正在修改代码",
                "mcpToolCall": "正在调用工具",
                "dynamicToolCall": "正在调用工具",
                "webSearch": "正在搜索资料",
                "collabToolCall": "正在协同处理任务",
            }.get(str(item_type), "正在处理任务")
            break
    if not current_action:
        current_action = (
            "正在处理任务" if status == "working" else
            "任务已完成" if status == "completed" else
            "等待新任务"
        )

    started = _epoch(latest_turn.get("startedAt") or latest_turn.get("createdAt"))
    completed = 0 if status in {"working", "waiting_user"} else _epoch(latest_turn.get("completedAt"))
    duration = max(0, (completed or now_epoch) - started) if started else 0
    plan_completed, plan_total = _plan_counts(latest_turn, latest_items)
    return {
        "status": status,
        "thread_id": _safe_line(str(thread.get("id") or ""), 63),
        "turn_id": _safe_line(str(latest_turn.get("id") or ""), 63),
        "title": _safe_line(str(thread.get("name") or thread.get("preview") or "暂无任务"), 95),
        "last_user_message": _safe_message(user_message),
        "last_assistant_message": _safe_line(assistant_message, 255),
        "reasoning_summary": "",
        "conversation_mode": conversation_mode,
        "codex_messages": safe_messages,
        "current_action": _safe_line(current_action, 127),
        "duration_seconds": duration,
        "user_message_count": user_message_count,
        # Retain the schema-v1 field for older firmware and web clients.
        "message_count": user_message_count,
        "token_count": token_count,
        "token_count_available": token_count > 0,
        "plan_completed": plan_completed,
        "plan_total": plan_total,
        "started_at_epoch": started,
        "last_updated_epoch": updated,
    }


class CodexAppServerSource:
    """Maintain a display-safe snapshot using only documented app-server calls."""

    MAX_DISPLAY_TASKS = 6
    SOURCE_KINDS = [
        "cli", "vscode", "exec", "appServer", "unknown",
    ]
    INTERNAL_THREAD_PREFIXES = (
        "The following is the Codex agent history",
    )

    def __init__(self, runtime_folder: Path, cwd: Path, codex_command: str | None = None) -> None:
        self.runtime_folder = runtime_folder
        self.cwd = cwd
        self.command = _resolve_codex_command(codex_command, runtime_folder)
        self.client = AppServerClient(self.command, cwd)
        self.account: dict[str, Any] = account_snapshot({}, {})
        self.task: dict[str, Any] = thread_snapshot(None)
        self.tasks: list[dict[str, Any]] = [self.task]
        self.active_thread_ids: set[str] = set()
        self.last_account_refresh = 0.0
        self._account_refresh_failures = 0
        self._thread_refresh_failures = 0
        self._has_account_snapshot = False
        self._has_thread_snapshot = False
        self._unreadable_thread_ids: set[str] = set()

    def start(self) -> None:
        self.client.start()
        account = self.client.request("account/read", {"refreshToken": False})
        account_info = account.get("account") if isinstance(account, dict) else None
        if not isinstance(account_info, dict) or account_info.get("type") != "chatgpt":
            raise AppServerError("App Server 未登录 ChatGPT；请先在 Codex CLI 中登录同一账户")
        self.refresh_account(force=True)
        self._has_account_snapshot = True

    def refresh_account(self, force: bool = False) -> None:
        if not force and time.monotonic() - self.last_account_refresh < 60:
            return
        rate = self.client.request("account/rateLimits/read")
        usage = self.client.request("account/usage/read")
        self.account = account_snapshot(rate, usage)
        self.last_account_refresh = time.monotonic()

    def refresh_thread(self) -> None:
        result = self.client.request("thread/list", {
            "limit": 20,
            "sortKey": "recency_at",
            "sortDirection": "desc",
            "sourceKinds": self.SOURCE_KINDS,
            "archived": False,
        })
        threads = result.get("data") if isinstance(result, dict) else None
        if not isinstance(threads, list) or not threads:
            self.task = thread_snapshot(None)
            self.tasks = [self.task]
            self.active_thread_ids.clear()
            return

        display_threads = [item for item in threads if self._is_display_thread(item)]
        unreadable_ids = getattr(self, "_unreadable_thread_ids", set())
        display_thread_ids = {
            str(item.get("id") or "") for item in display_threads if isinstance(item, dict)
        }
        unreadable_ids.intersection_update(display_thread_ids)
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for selected in display_threads:
            thread_id = str(selected.get("id") or "")
            if not thread_id or thread_id in seen or thread_id in unreadable_ids:
                continue
            if (self._is_active_hint(selected) or thread_id in self.active_thread_ids or
                    len(candidates) < self.MAX_DISPLAY_TASKS):
                candidates.append(selected)
                seen.add(thread_id)

        active: list[dict[str, Any]] = []
        fallback: dict[str, Any] | None = None
        unreadable = 0
        for selected in candidates:
            try:
                detail = self.client.request("thread/read", {"threadId": selected["id"], "includeTurns": True})
            except (AppServerError, OSError, ValueError) as error:
                # One stored thread may contain an item type unknown to this
                # App Server build. Keep the other readable tasks flowing.
                unreadable += 1
                thread_id = str(selected.get("id") or "")
                if thread_id not in unreadable_ids:
                    print(f"Codex 任务读取失败，已跳过该任务：{error}")
                unreadable_ids.add(thread_id)
                continue
            thread = detail.get("thread") if isinstance(detail, dict) else None
            if not self._is_display_thread(thread):
                continue
            snapshot = thread_snapshot(thread)
            if fallback is None:
                fallback = snapshot
            if snapshot["status"] in {"working", "waiting_user"}:
                active.append(snapshot)

        if active:
            active.sort(key=lambda item: (
                int(item.get("started_at_epoch") or 0),
                str(item.get("thread_id") or ""),
            ))
            active = active[:self.MAX_DISPLAY_TASKS]
            self.tasks = active
            self.task = active[0]
            self.active_thread_ids = {str(item["thread_id"]) for item in active}
            return

        self._unreadable_thread_ids = unreadable_ids
        if fallback is not None:
            self.active_thread_ids.clear()
            self.task = fallback
            self.tasks = [self.task]
        elif unreadable:
            # Do not erase the last valid task view when every selected
            # historical thread is temporarily unreadable.
            print("Codex 没有可读取的任务，保留上一次成功的任务快照")

    @staticmethod
    def _is_active_hint(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        runtime = item.get("status") if isinstance(item.get("status"), dict) else {}
        if str(runtime.get("type", "")).lower() == "active":
            return True
        active_flags = {str(flag).lower() for flag in runtime.get("activeFlags", []) if isinstance(flag, str)}
        if any("waiting" in flag or "approval" in flag for flag in active_flags):
            return True
        recency = _epoch(item.get("recencyAt"))
        if (str(runtime.get("type", "")).lower() == "notloaded" and recency > 0 and
                0 <= int(time.time()) - recency < 15 * 60):
            return True
        turns = [turn for turn in item.get("turns", []) if isinstance(turn, dict)]
        if not turns:
            return False
        latest = turns[-1]
        status = str(latest.get("status", "")).replace("_", "").lower()
        if status in {"inprogress", "running", "active"}:
            return True
        if status == "interrupted" and latest.get("completedAt") is None:
            updated = _epoch(item.get("updatedAt") or latest.get("updatedAt") or latest.get("startedAt"))
            return updated > 0 and int(time.time()) - updated < 15 * 60
        return False

    @classmethod
    def _is_display_thread(cls, item: Any) -> bool:
        if not isinstance(item, dict) or not item.get("id"):
            return False
        candidates: list[str] = []
        for key in ("name", "preview"):
            value = item.get(key)
            if isinstance(value, str):
                candidates.append(value)
        for turn in item.get("turns", []):
            if not isinstance(turn, dict):
                continue
            for entry in turn.get("items", []):
                if isinstance(entry, dict) and entry.get("type") == "userMessage":
                    value = _text(entry.get("content"))
                    if value:
                        candidates.append(value)
        return not any(
            text.strip().startswith(prefix)
            for text in candidates
            for prefix in cls.INTERNAL_THREAD_PREFIXES
        )

    def snapshot(self) -> dict[str, Any]:
        for label, refresh, failure_name, cache_name in (
            ("账户额度", self.refresh_account, "_account_refresh_failures", "_has_account_snapshot"),
            ("任务状态", self.refresh_thread, "_thread_refresh_failures", "_has_thread_snapshot"),
        ):
            try:
                refresh()
                setattr(self, failure_name, 0)
                setattr(self, cache_name, True)
            except (AppServerError, OSError, ValueError) as error:
                failures = int(getattr(self, failure_name, 0)) + 1
                setattr(self, failure_name, failures)
                if (not bool(getattr(self, cache_name, False)) or
                        failures >= TRANSIENT_REFRESH_FAILURE_LIMIT):
                    raise
                print(f"Codex {label}暂时无法刷新，继续使用上次成功数据：{error}")
        return {
            "schema_version": 1,
            "generated_at_epoch": int(time.time()),
            "preview_data": False,
            "source": "codex_app_server",
            "codex": {**self.account, "task": self.task, "tasks": self.tasks},
        }

    def close(self) -> None:
        self.client.close()


def run_collector(
    runtime_folder: Path,
    cwd: Path,
    interval: float,
    stop: threading.Event,
    publish: Callable[[dict[str, Any]], None],
    codex_command: str | None = None,
    report: Callable[[str, str], None] | None = None,
    restart: threading.Event | None = None,
    enabled: Callable[[], bool] | None = None,
) -> None:
    backoff = 2.0
    while not stop.is_set():
        if enabled is not None and not enabled():
            if report is not None:
                report("disabled", "Codex 模块已关闭")
            while not stop.is_set() and not (restart is not None and restart.is_set()):
                stop.wait(0.25)
            if restart is not None:
                restart.clear()
            backoff = 2.0
            continue
        source: CodexAppServerSource | None = None
        try:
            source = CodexAppServerSource(runtime_folder, cwd, codex_command)
            source.start()
            print("Codex App Server 已连接，正在读取账户与任务状态")
            if report is not None:
                report("online", "Codex App Server 已连接")
            backoff = 2.0
            while (not stop.is_set()
                   and not (restart is not None and restart.is_set())
                   and (enabled is None or enabled())):
                publish(source.snapshot())
                if report is not None:
                    report("online", "数据采集正常")
                deadline = time.monotonic() + max(1.0, interval)
                while (not stop.is_set()
                       and not (restart is not None and restart.is_set())
                       and (enabled is None or enabled())):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    stop.wait(min(0.25, remaining))
        except (AppServerError, OSError, ValueError) as error:
            print(f"Codex App Server 暂时不可用：{error}")
            if report is not None:
                report("error", str(error))
            deadline = time.monotonic() + backoff
            while (not stop.is_set()
                   and not (restart is not None and restart.is_set())
                   and (enabled is None or enabled())):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                stop.wait(min(0.25, remaining))
            backoff = min(30.0, backoff * 2)
        finally:
            if source is not None:
                source.close()
        if restart is not None and restart.is_set():
            restart.clear()
            backoff = 2.0
            if report is not None:
                if enabled is not None and not enabled():
                    report("disabled", "Codex 模块已关闭")
                else:
                    report("starting", "正在重新连接 Codex App Server")


def probe_app_server(runtime_folder: Path, cwd: Path, codex_command: str | None = None) -> dict[str, Any]:
    """Run a read-only health check against the documented App Server APIs."""
    checked_at = int(time.time())
    result: dict[str, Any] = {
        "ok": False,
        "checked_at_epoch": checked_at,
        "cli": {"available": False, "version": "--", "source": "--"},
        "app_server": {"available": False},
        "account": {"logged_in": False, "email": "", "plan_type": ""},
        "checks": {"rate_limits": False, "usage": False, "threads": False},
        "thread_count": 0,
        "detail": "尚未完成检测",
    }
    try:
        command = _resolve_codex_command(codex_command, runtime_folder)
    except (AppServerError, OSError) as error:
        result["detail"] = str(error)
        return result

    command_path = Path(command[-1]).resolve()
    package_path = command_path.parent.parent / "package.json" if command_path.suffix.lower() == ".js" else None
    version = "--"
    if package_path is not None and package_path.is_file():
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
            version = str(package.get("version") or "--") if isinstance(package, dict) else "--"
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    app_tools = (application_root() / "tools").resolve()
    runtime_cli = (runtime_folder / "codex-cli").resolve()
    if command_path == app_tools or app_tools in command_path.parents:
        source = "应用内置"
    elif command_path == runtime_cli or runtime_cli in command_path.parents:
        source = "本机私有"
    else:
        source = "系统回退"
    result["cli"] = {"available": True, "version": version, "source": source}

    client = AppServerClient(command, cwd)
    try:
        try:
            client.start()
            result["app_server"]["available"] = True
            account = client.request("account/read", {"refreshToken": False})
        except (AppServerError, OSError, ValueError) as error:
            result["detail"] = f"App Server 检测失败：{error}"
            return result

        account_info = account.get("account") if isinstance(account, dict) else None
        logged_in = isinstance(account_info, dict) and account_info.get("type") == "chatgpt"
        if not logged_in:
            result["detail"] = "App Server 可启动，但尚未登录 ChatGPT/Codex"
            return result
        result["account"] = {
            "logged_in": True,
            "email": _safe_line(str(account_info.get("email") or ""), 160),
            "plan_type": _safe_line(str(account_info.get("planType") or ""), 40),
        }

        calls = (
            ("rate_limits", "account/rateLimits/read", None),
            ("usage", "account/usage/read", None),
            ("threads", "thread/list", {
                "limit": 20,
                "sortKey": "recency_at",
                "sortDirection": "desc",
                "sourceKinds": CodexAppServerSource.SOURCE_KINDS,
                "archived": False,
            }),
        )
        errors: list[str] = []
        for key, method, params in calls:
            try:
                response = client.request(method, params)
                result["checks"][key] = True
                if key == "threads":
                    threads = response.get("data") if isinstance(response, dict) else None
                    result["thread_count"] = len(threads) if isinstance(threads, list) else 0
            except (AppServerError, OSError, ValueError) as error:
                errors.append(f"{key}: {_safe_line(str(error), 180)}")

        result["ok"] = all(result["checks"].values())
        result["detail"] = (
            "Codex CLI、App Server、账户、额度、用量与任务列表均可正常读取"
            if result["ok"] else "部分只读接口检测失败：" + "；".join(errors)
        )
        return result
    finally:
        client.close()
