"""Claude Code 工作状态采集.

通过 Claude Code 官方 hooks 事件推送聚合各会话的任务状态。只消费
``session_id`` / ``cwd`` / ``hook_event_name`` / ``message``（通知类型判断）
这几个状态字段，不解析、不存储消息内容。权限请求转发（PermissionRequest
hook → Dotii 屏上批准）见 `PermissionBroker`。
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

MAX_SESSIONS = 16
SESSION_TTL_SECONDS = 24 * 3600.0
SNAPSHOT_SESSIONS = 4
PROJECT_MAX_BYTES = 47
EVENT_URL_PATH = "/api/v1/admin/claudecode/event"
PERMISSION_URL_PATH = "/api/v1/admin/claudecode/permission"
PERMISSION_WAIT_SECONDS = 300
PERMISSION_MAX_PENDING = 4
PERMISSION_PREVIEW_BYTES = 90

# hook 事件 → 会话状态（与 codex.task.status 同一状态集合，供固件复用）
EVENT_STATUS = {
    "UserPromptSubmit": "working",
    "PostToolUse": "working",
    "PostToolUseFailure": "working",
    "Stop": "completed",
    "StopFailure": "failed",
    "SessionStart": "idle",
}
REMOVE_EVENTS = {"SessionEnd"}
# Notification 事件按 message 内容判断是否需要用户介入
NOTIFICATION_PATTERN = re.compile(
    r"permission|idle|needs? input|agent_needs_input|等待|需要你|需要您", re.IGNORECASE
)

STATUS_TEXT = {
    "working": "工作中",
    "waiting_user": "等待用户",
    "completed": "已完成",
    "failed": "失败",
    "idle": "暂无任务",
    "offline": "离线",
}

HOOK_EVENTS = (
    "SessionStart", "SessionEnd", "UserPromptSubmit",
    "PostToolUse", "Stop", "StopFailure", "Notification",
)


def hook_command(port: int) -> str:
    """裸 curl 命令：macOS/Linux 的 shell 与 Windows 的 cmd 都能直接执行，
    stdin 由 Claude Code 直接送入 curl，不依赖 sh 外壳或单引号语法。
    事件上报是尽力而为——管理中心离线（如重启间隙）时静默退出 0，
    不在 Claude Code 界面上产生 hook 错误提示。"""
    return (
        f"curl -s -m 2 -X POST http://127.0.0.1:{port}{EVENT_URL_PATH} "
        f"-H \"Content-Type: application/json\" --data-binary @- || exit 0"
    )


def permission_hook_command(port: int) -> str:
    """PermissionRequest 决策 hook：同裸 curl 形态，但服务端会 hold 响应
    等待 Dotii 上的批准（上限 300s），curl 超时留 10s 余量。管理中心
    不可达时静默退出 0——空输出让 Claude Code 照常弹本机权限提示。"""
    return (
        f"curl -s -m 310 -X POST http://127.0.0.1:{port}{PERMISSION_URL_PATH} "
        f"-H \"Content-Type: application/json\" --data-binary @- || exit 0"
    )


def _project_name(cwd: Any) -> str:
    text = str(cwd or "").rstrip("/\\")
    name = text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if text else ""
    return name.encode("utf-8")[:PROJECT_MAX_BYTES].decode("utf-8", errors="ignore")


class ClaudeCodeMonitor:
    """内存聚合器：事件驱动，无后台线程。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}

    def record_event(self, payload: dict[str, Any], now: float | None = None) -> str | None:
        """记录一条 hook 事件；返回映射后的状态，未映射事件返回 None。"""
        now = time.time() if now is None else now
        if not isinstance(payload, dict):
            return None
        event = str(payload.get("hook_event_name") or "")
        session_id = str(payload.get("session_id") or "")
        if not session_id or len(session_id) > 128:
            return None
        if event in REMOVE_EVENTS:
            with self._lock:
                self._sessions.pop(session_id, None)
            return "offline"
        status = EVENT_STATUS.get(event)
        if status is None and event == "Notification":
            if NOTIFICATION_PATTERN.search(str(payload.get("message") or "")):
                status = "waiting_user"
        if status is None:
            return None
        session = {"status": status, "project": _project_name(payload.get("cwd")), "updated_at": now}
        with self._lock:
            self._sessions[session_id] = session
            while len(self._sessions) > MAX_SESSIONS:
                oldest = min(self._sessions, key=lambda key: self._sessions[key]["updated_at"])
                del self._sessions[oldest]
        return status

    def set_enabled(self, enabled: bool) -> None:
        if not enabled:
            with self._lock:
                self._sessions.clear()

    def snapshot(self, module_enabled: bool = True, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._lock:
            sessions = [dict(value) for value in self._sessions.values()]
        sessions = [item for item in sessions if now - item["updated_at"] <= SESSION_TTL_SECONDS]
        sessions.sort(key=lambda item: item["updated_at"], reverse=True)
        primary = sessions[0] if sessions else None
        if not module_enabled:
            service_state, status, project = "disabled", "idle", ""
        elif primary is None:
            service_state, status, project = "idle", "idle", ""
        else:
            service_state, status, project = "online", primary["status"], primary["project"]
        return {
            "module_enabled": module_enabled,
            "service_state": service_state,
            "configured": True,
            "connected": service_state == "online",
            "source": "claude_code_hooks",
            "status": status,
            "status_text": STATUS_TEXT.get(status, "离线"),
            "project": project,
            "session_count": len(sessions),
            "sessions": [
                {
                    "status": item["status"],
                    "status_text": STATUS_TEXT.get(item["status"], "离线"),
                    "project": item["project"],
                    "updated_at_epoch": int(item["updated_at"]),
                }
                for item in sessions[:SNAPSHOT_SESSIONS]
            ],
            "updated_at_epoch": int(primary["updated_at"]) if primary else 0,
        }


# 权限请求的 tool_input 摘要字段优先级（拿不到再退 str 截断）
_PREVIEW_FIELDS = {
    "Bash": ("command", "description"),
    "WebFetch": ("url", "prompt"),
    "WebSearch": ("query",),
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "NotebookEdit": ("notebook_path",),
}


def _truncate_utf8(text: str, limit: int) -> str:
    """按字节限制截断且不撕裂 UTF-8 字符，折叠空白便于圆屏展示。"""
    compact = " ".join(str(text).split())
    encoded = compact.encode("utf-8")
    if len(encoded) <= limit:
        return compact
    return encoded[:limit].decode("utf-8", errors="ignore").rstrip() or "…"


def _preview_text(tool_name: str, tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return _truncate_utf8(tool_input if tool_input else tool_name, PERMISSION_PREVIEW_BYTES)
    for field in _PREVIEW_FIELDS.get(tool_name, ("description", "file_path", "url", "command")):
        value = tool_input.get(field)
        if value:
            return _truncate_utf8(value, PERMISSION_PREVIEW_BYTES)
    return tool_name


class PermissionBroker:
    """PermissionRequest hook 的等待-决策队列.

    hook 线程调 ``submit_and_wait`` 阻塞到 Dotii/管理页给出决策或超时；
    设备、管理页、蓝牙链路三个入口都走 ``resolve``。多会话并发时按
    FIFO 排队（每条独立 hold、独立超时），快照只输出队首给圆屏、
    完整队列给管理页。超时/模式关闭返回 None——hook 输出空体，
    Claude Code 照常弹本机权限提示（fail-open）。
    """

    def __init__(self, module_enabled: "Callable[[], bool] | None" = None) -> None:
        self._lock = threading.Lock()
        self._enabled = False
        # Claude Code 模块开关读取（注入，测试传 None 跳过）：模块关闭时
        # 权限请求直接 fail-open，不让 hook 空等 300 秒。
        self._module_enabled = module_enabled
        self._pending: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

    def set_mode(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)
            if not self._enabled:
                # 关闭模式：唤醒所有等待中的 hook（空体回落本机提示）。
                for entry in self._pending.values():
                    entry["event"].set()

    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def submit_and_wait(self, payload: Any, timeout: float = PERMISSION_WAIT_SECONDS) -> dict[str, Any] | None:
        """注册一条权限请求并阻塞等待决策；返回 ``{"behavior": ...}`` 或 None。"""
        if not isinstance(payload, dict):
            return None
        if self._module_enabled is not None and not self._module_enabled():
            return None
        tool_name = str(payload.get("tool_name") or "")
        if not tool_name:
            return None
        request_id = secrets.token_hex(6)
        entry = {
            "tool": _truncate_utf8(tool_name, 20),
            "preview": _preview_text(tool_name, payload.get("tool_input")),
            "project": _project_name(payload.get("cwd")),
            "expires_at": time.time() + timeout,
            "event": threading.Event(),
            "decision": None,
        }
        with self._lock:
            if not self._enabled:
                return None
            self._pending[request_id] = entry
            while len(self._pending) > PERMISSION_MAX_PENDING:
                _, oldest = self._pending.popitem(last=False)
                oldest["event"].set()  # 被挤掉的请求空体回落本机提示
        entry["event"].wait(timeout)
        with self._lock:
            self._pending.pop(request_id, None)
            return entry["decision"]

    def resolve(self, request_id: str, allow: bool) -> bool:
        """注入决策（设备/管理页/蓝牙三入口）；唤醒对应 hook 线程。"""
        if not isinstance(request_id, str):
            return False
        with self._lock:
            entry = self._pending.get(request_id)
            if entry is None:
                return False
            entry["decision"] = {"behavior": "allow" if allow else "deny"}
            entry["event"].set()
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            queue = [
                {
                    "id": request_id,
                    "tool": entry["tool"],
                    "preview": entry["preview"],
                    "project": entry["project"],
                    "expires_epoch": int(entry["expires_at"]),
                }
                for request_id, entry in self._pending.items()
            ]
        head = queue[0] if queue else None
        return {
            "enabled": self._enabled,
            "wait_seconds": PERMISSION_WAIT_SECONDS,
            "pending": None if head is None else {**head, "queued": len(queue) - 1},
            "queue": queue,
        }


def _is_our_entry(entry: Any) -> bool:
    """识别我们注入的 matcher 组：组的 hooks 里含事件上报或权限决策命令。"""
    if not isinstance(entry, dict):
        return False
    commands = [str(hook.get("command", "")) for hook in entry.get("hooks", []) if isinstance(hook, dict)]
    return any((EVENT_URL_PATH in command or PERMISSION_URL_PATH in command) for command in commands)


def hooks_installed(path: Path) -> bool:
    """检查设置文件中是否仍有我们的上报条目（只读，异常一律视为未安装）。"""
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(settings, dict):
        return False
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        if any(_is_our_entry(entry) for entry in entries):
            return True
    return False


def _our_command(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    for hook in entry.get("hooks", []):
        if isinstance(hook, dict):
            command = str(hook.get("command", ""))
            if EVENT_URL_PATH in command or PERMISSION_URL_PATH in command:
                return command
    return None


def update_hooks(path: Path, port: int, action: str = "install") -> dict[str, Any]:
    """在 Claude Code 设置文件中安装/移除事件上报 hooks（幂等，保留用户已有配置）。

    Claude Code 的 hooks 采用嵌套结构：事件 → matcher 组列表 → 每组含
    ``hooks`` 命令数组。我们为每个事件追加一个独立 matcher 组。首次写入前
    备份为 ``<settings.json>.dotii-backup``（已存在时不覆盖，保留最早原始
    状态）；任何格式异常都抛出 ValueError 且不改动文件。
    """
    if action not in {"install", "remove"}:
        raise ValueError("action 必须是 install 或 remove")
    try:
        raw = path.read_text(encoding="utf-8") if path.is_file() else "{}"
        settings = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取 Claude Code 设置文件：{error.__class__.__name__}") from error
    if not isinstance(settings, dict):
        raise ValueError("Claude Code 设置文件格式不受支持")
    hooks = settings.get("hooks") if "hooks" in settings else {}
    if not isinstance(hooks, dict):
        raise ValueError("Claude Code 设置文件的 hooks 配置格式不受支持")

    command = hook_command(port)
    permission_command = permission_hook_command(port)
    # 事件上报组 + 权限决策组一起安装/移除；决策 hook 装上即无害（远程
    # 批准模式关闭时服务端立即返回空体，Claude Code 照常弹本机提示）。
    install_plan = [(event, command) for event in HOOK_EVENTS]
    install_plan.append(("PermissionRequest", permission_command))
    changed: list[str] = []
    for event, event_command in install_plan:
        entries = hooks.get(event, [])
        if not isinstance(entries, list):
            raise ValueError(f"hooks.{event} 格式不受支持")
        ours = [entry for entry in entries if _is_our_entry(entry)]
        if action == "install":
            if len(ours) == 1 and _our_command(ours[0]) == event_command:
                continue
            remaining = [entry for entry in entries if not _is_our_entry(entry)]
            hooks[event] = remaining + [{"matcher": "", "hooks": [{"type": "command", "command": event_command}]}]
            changed.append(event)
        else:
            if not ours:
                continue
            remaining = [entry for entry in entries if not _is_our_entry(entry)]
            if remaining:
                hooks[event] = remaining
            else:
                hooks.pop(event, None)
            changed.append(event)
    if not changed:
        return {"ok": True, "action": action, "changed": [], "settings_path": str(path)}
    settings["hooks"] = hooks
    backup = path.with_name(path.name + ".dotii-backup")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and not backup.is_file():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    temporary = path.with_name(path.name + ".dotii-tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return {"ok": True, "action": action, "changed": changed, "settings_path": str(path)}
