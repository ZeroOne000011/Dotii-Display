#!/usr/bin/env python3
"""LAN bridge and loopback-only management UI for Dotii.

The ESP32-facing schema v1 endpoint is intentionally kept compatible with the
current firmware. Administrative data and static UI assets are only available
from the local computer.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
import zlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from codex_app_server import probe_app_server, run_collector
from bambu_client import BambuConfigStore, BambuService
from bluetooth_bridge import BluetoothBridge
from firmware_flasher import FirmwareFlasher
from runtime_paths import application_root, is_frozen, project_root, resource_path, sibling_executable, tools_root


SCHEMA_VERSION = 1
MAX_BODY = 32 * 1024
CUSTOM_FRAME_WIDTH = 466
CUSTOM_FRAME_HEIGHT = 466
CUSTOM_FRAME_SIZE = CUSTOM_FRAME_WIDTH * CUSTOM_FRAME_HEIGHT * 2
MAX_CUSTOM_SOURCE_SIZE = 4 * 1024 * 1024
MAX_DISPLAY_TASKS = 6
ALLOWED_STATUSES = {"working", "waiting_user", "completed", "failed", "idle", "offline"}
ALLOWED_CONVERSATION_MODES = {"progress", "history", "final", "empty"}
CUSTOM_DEFAULTS = {
    "enabled": True,
    "revision": 0,
    "title": "我的页面",
    "value": "你好，Dotii",
    "body": "在电脑端编辑内容，保存后会自动同步到设备。",
    "footer": "自定义内容",
    "accent": "#F2C66D",
    "title_visible": True,
    "title_color": "#8E9B97",
    "value_visible": True,
    "value_color": "#F2C66D",
    "body_visible": True,
    "body_color": "#D6DFDC",
    "footer_visible": True,
    "footer_color": "#8E9B97",
    "background_image": False,
    "image_fit": "cover",
    "image_opacity": 70,
    "ring_enabled": True,
    "ring_start": "#F2C66D",
    "ring_end": "#5DA9FF",
}
MODULE_DEFAULTS = {"codex": False, "bambu": False, "dotii": True}


def resolve_ffmpeg(runtime_folder: Path) -> str | None:
    """Resolve the bundled FFmpeg executable, with legacy user fallbacks."""
    candidates = [
        runtime_folder / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        tools_root() / "ffmpeg" / "bin" / "ffmpeg.exe",
    ]
    candidate = next((path for path in candidates if path.is_file()), None)
    return str(candidate.resolve()) if candidate else shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
DOTII_EXPRESSION_IDS = (
    "idle_breath", "blink", "curious", "sleepy_yawn",
    "touch_response", "connecting", "working", "complete", "failure",
)
DOTII_EXPRESSIONS = set(DOTII_EXPRESSION_IDS)
DOTII_LEGACY_EXPRESSIONS = {"happy"}
DOTII_FIXED_EXPRESSION_IDS = (
    "idle_breath", "blink", "sleepy_yawn", "touch_response", "connecting",
)
DOTII_BUSINESS_EXPRESSION_IDS = (
    "curious", "working", "complete", "failure",
)
DOTII_STATE_GROUPS = (
    {"id": "codex", "label": "Codex 状态"},
    {"id": "bambu", "label": "Bambu 状态"},
)
DOTII_FIXED_STATE_DEFINITIONS = (
    {"id": "idle", "label": "空闲", "expression": "idle_breath"},
    {"id": "blink", "label": "待机眨眼", "expression": "blink"},
    {"id": "long_idle", "label": "长时间无操作", "expression": "sleepy_yawn"},
    {"id": "touch", "label": "触摸屏幕", "expression": "touch_response"},
    {"id": "connecting", "label": "连接中", "expression": "connecting"},
)
DOTII_BUSINESS_STATE_DEFINITIONS = (
    {"id": "codex_waiting_user", "label": "等待操作", "group": "codex"},
    {"id": "codex_working", "label": "工作中", "group": "codex"},
    {"id": "codex_completed", "label": "任务完成", "group": "codex"},
    {"id": "codex_failure", "label": "失败或异常", "group": "codex"},
    {"id": "bambu_paused", "label": "已暂停", "group": "bambu"},
    {"id": "bambu_printing", "label": "打印中", "group": "bambu"},
    {"id": "bambu_completed", "label": "打印完成", "group": "bambu"},
    {"id": "bambu_failure", "label": "故障或异常", "group": "bambu"},
)
DOTII_FIXED_STATE_EXPRESSIONS = {
    definition["id"]: definition["expression"]
    for definition in DOTII_FIXED_STATE_DEFINITIONS
}
DOTII_BUSINESS_STATE_LABELS = {
    definition["id"]: definition["label"]
    for definition in DOTII_BUSINESS_STATE_DEFINITIONS
}
DOTII_LEGACY_STATE_EXPANSIONS = {
    "waiting_user": ("codex_waiting_user", "bambu_paused"),
    "working": ("codex_working", "bambu_printing"),
    "completed": ("codex_completed",),
    "print_completed": ("bambu_completed",),
    "failure": ("codex_failure", "bambu_failure"),
}
DOTII_DEFAULT_BUSINESS_ASSIGNMENTS = {
    "curious": ("codex_waiting_user", "bambu_paused"),
    "working": ("codex_working", "bambu_printing"),
    "complete": ("codex_completed", "bambu_completed"),
    "failure": ("codex_failure", "bambu_failure"),
}
DOTII_DEFAULT_BUSINESS_EXPRESSION = {
    state_id: expression_id
    for expression_id, state_ids in DOTII_DEFAULT_BUSINESS_ASSIGNMENTS.items()
    for state_id in state_ids
}
DOTII_LOOP_DURATIONS_MS = {
    expression_id: 1200 if expression_id == "touch_response" else 800
    for expression_id in DOTII_EXPRESSION_IDS
}
DOTII_STATE_DURATION_OPTIONS = (
    {"value": 1000, "label": "1 秒"},
    {"value": 3000, "label": "3 秒"},
    {"value": 5000, "label": "5 秒"},
    {"value": 30000, "label": "30 秒"},
    {"value": 0, "label": "保持"},
)
DOTII_STATE_DURATION_VALUES = {
    option["value"] for option in DOTII_STATE_DURATION_OPTIONS
}
DISPLAY_DEFAULTS = {
    "revision": 0,
    "codex_ui": "classic",
    "docked_rotation_tenths": 840,
    "screen_off_timeout_seconds": 60,
    "sleep_timeout_seconds": 300,
    "charging_screen_off_timeout_seconds": 60,
    "charging_sleep_timeout_seconds": 300,
    "screen_off_page": "none",
}
DISPLAY_TIMEOUT_OPTIONS = {0, 10, 30, 60, 180, 300, 600, 1800}
CODEX_UI_OPTIONS = {"classic", "dual_limit"}
SCREEN_OFF_PAGE_OPTIONS = {"none", "custom", "dotii"}
CUSTOM_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
WEB_ROOT = resource_path("web")
STARTUP_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_REGISTRY_VALUE = "DotiiManagementCenter"
MANAGEMENT_CENTER_EXECUTABLE = "DotiiManagementCenter.exe"
CODEX_CLI_PACKAGE = os.environ.get("STATE_DISPLAY_CODEX_PACKAGE", "@openai/codex@0.151.0")
CODEX_CLI_VERSION = "0.151.0"


def _bounded_text(value: Any, maximum: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    return value.encode("utf-8")[:maximum].decode("utf-8", errors="ignore")


def startup_command(python_executable: Path, app_script: Path) -> str:
    return f'"{python_executable.resolve()}" -B "{app_script.resolve()}" --startup'


def packaged_startup_command(management_center: Path) -> str:
    """Build the login command for the tray host, never the bridge backend."""
    return f'"{management_center.resolve()}" --startup'


def current_startup_command() -> str:
    if is_frozen():
        return packaged_startup_command(sibling_executable(MANAGEMENT_CENTER_EXECUTABLE))
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    app_script = application_root() / "bridge" / "bridge_app.py"
    return startup_command(pythonw, app_script)


def startup_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REGISTRY_KEY) as key:
            winreg.QueryValueEx(key, STARTUP_REGISTRY_VALUE)
            return True
    except (FileNotFoundError, OSError):
        return False


def set_startup(enabled: bool) -> bool:
    if os.name != "nt":
        return False
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, STARTUP_REGISTRY_KEY) as key:
        if enabled:
            winreg.SetValueEx(
                key, STARTUP_REGISTRY_VALUE, 0, winreg.REG_SZ, current_startup_command()
            )
        else:
            try:
                winreg.DeleteValue(key, STARTUP_REGISTRY_VALUE)
            except FileNotFoundError:
                pass
    return startup_enabled()


def load_or_create_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        token = ""
    if token:
        return token
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    return token


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def local_ipv4() -> str:
    """Return the LAN address used in the device connection hint."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        address = sock.getsockname()[0]
        return address if address and not _is_loopback(address) else "127.0.0.1"
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _unbounded_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    return value


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = int(value)
    if result < 0:
        raise ValueError(f"{field} must be non-negative")
    return result


def _validated_messages(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("codex_messages must be a list")
    result: list[str] = []
    for index, message in enumerate(value):
        if not isinstance(message, str):
            raise ValueError(f"codex_messages[{index}] must be text")
        if message:
            result.append(message)
    return result


def _validate_codex_task(task: Any) -> dict[str, Any]:
    if not isinstance(task, dict):
        raise ValueError("codex task must be an object")
    status = task.get("status")
    if status not in ALLOWED_STATUSES:
        raise ValueError("unsupported task status")
    conversation_mode = task.get(
        "conversation_mode", "final" if status in {"completed", "failed"} else "progress"
    )
    if conversation_mode not in ALLOWED_CONVERSATION_MODES:
        raise ValueError("unsupported conversation mode")
    return {
        "status": status,
        "thread_id": _bounded_text(task.get("thread_id", ""), 63, "thread_id"),
        "turn_id": _bounded_text(task.get("turn_id", ""), 63, "turn_id"),
        "title": _bounded_text(task.get("title", ""), 95, "title"),
        "last_user_message": _unbounded_text(
            task.get("last_user_message", ""), "last_user_message"
        ),
        "last_assistant_message": _bounded_text(
            task.get("last_assistant_message", ""), 255, "last_assistant_message"
        ),
        "reasoning_summary": _bounded_text(
            task.get("reasoning_summary", ""), 383, "reasoning_summary"
        ),
        "conversation_mode": conversation_mode,
        "codex_messages": _validated_messages(
            task.get("codex_messages", [task.get("last_assistant_message", "")])
        ),
        "current_action": _bounded_text(task.get("current_action", ""), 127, "current_action"),
        "duration_seconds": _non_negative_int(task.get("duration_seconds", 0), "duration_seconds"),
        "user_message_count": _non_negative_int(
            task.get("user_message_count", task.get("message_count", 0)), "user_message_count"
        ),
        "message_count": _non_negative_int(
            task.get("user_message_count", task.get("message_count", 0)), "message_count"
        ),
        "token_count": _non_negative_int(task.get("token_count", 0), "token_count"),
        "token_count_available": bool(task.get("token_count_available", True)),
        "plan_completed": _non_negative_int(task.get("plan_completed", 0), "plan_completed"),
        "plan_total": _non_negative_int(task.get("plan_total", 0), "plan_total"),
        "started_at_epoch": _non_negative_int(task.get("started_at_epoch", 0), "started_at_epoch"),
        "last_updated_epoch": _non_negative_int(
            task.get("last_updated_epoch", 0), "last_updated_epoch"
        ),
    }


def _custom_text(payload: dict[str, Any], field: str, maximum: int, *, required: bool = False) -> str:
    value = payload.get(field, CUSTOM_DEFAULTS[field])
    if not isinstance(value, str):
        raise ValueError(f"custom.{field} must be text")
    value = value.strip()
    if required and not value:
        raise ValueError(f"custom.{field} is required")
    if len(value.encode("utf-8")) > maximum:
        raise ValueError(f"custom.{field} exceeds {maximum} UTF-8 bytes")
    return value


def _custom_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field, CUSTOM_DEFAULTS[field])
    if not isinstance(value, bool):
        raise ValueError(f"custom.{field} must be a boolean")
    return value


def _custom_color(payload: dict[str, Any], field: str, fallback: str | None = None) -> str:
    value = payload.get(field, fallback if fallback is not None else CUSTOM_DEFAULTS[field])
    if not isinstance(value, str) or CUSTOM_COLOR_PATTERN.fullmatch(value) is None:
        raise ValueError(f"custom.{field} must be a #RRGGBB color")
    return value.upper()


def validate_custom_config(payload: Any) -> dict[str, Any]:
    if payload is None:
        return dict(CUSTOM_DEFAULTS)
    if not isinstance(payload, dict):
        raise ValueError("custom must be an object")
    enabled = payload.get("enabled", CUSTOM_DEFAULTS["enabled"])
    if not isinstance(enabled, bool):
        raise ValueError("custom.enabled must be a boolean")
    accent = payload.get("accent", CUSTOM_DEFAULTS["accent"])
    if not isinstance(accent, str) or CUSTOM_COLOR_PATTERN.fullmatch(accent) is None:
        raise ValueError("custom.accent must be a #RRGGBB color")
    title_visible = _custom_bool(payload, "title_visible")
    value_visible = _custom_bool(payload, "value_visible")
    body_visible = _custom_bool(payload, "body_visible")
    footer_visible = _custom_bool(payload, "footer_visible")
    title_color = _custom_color(payload, "title_color")
    value_color = _custom_color(payload, "value_color", accent)
    body_color = _custom_color(payload, "body_color")
    footer_color = _custom_color(payload, "footer_color")
    background_image = payload.get("background_image", False)
    ring_enabled = payload.get("ring_enabled", True)
    if not isinstance(background_image, bool):
        raise ValueError("custom.background_image must be a boolean")
    if not isinstance(ring_enabled, bool):
        raise ValueError("custom.ring_enabled must be a boolean")
    image_fit = payload.get("image_fit", "cover")
    if image_fit not in {"cover", "contain"}:
        raise ValueError("custom.image_fit must be cover or contain")
    image_opacity = _non_negative_int(payload.get("image_opacity", 70), "custom.image_opacity")
    if image_opacity > 100:
        raise ValueError("custom.image_opacity must be between 0 and 100")
    ring_start = payload.get("ring_start", accent)
    ring_end = payload.get("ring_end", ring_start)
    if not isinstance(ring_start, str) or CUSTOM_COLOR_PATTERN.fullmatch(ring_start) is None:
        raise ValueError("custom.ring_start must be a #RRGGBB color")
    if not isinstance(ring_end, str) or CUSTOM_COLOR_PATTERN.fullmatch(ring_end) is None:
        raise ValueError("custom.ring_end must be a #RRGGBB color")
    return {
        "enabled": enabled,
        "revision": _non_negative_int(payload.get("revision", 0), "custom.revision"),
        "title": _custom_text(payload, "title", 48),
        "value": _custom_text(payload, "value", 96),
        "body": _custom_text(payload, "body", 256),
        "footer": _custom_text(payload, "footer", 96),
        "accent": accent.upper(),
        "title_visible": title_visible,
        "title_color": title_color,
        "value_visible": value_visible,
        "value_color": value_color,
        "body_visible": body_visible,
        "body_color": body_color,
        "footer_visible": footer_visible,
        "footer_color": footer_color,
        "background_image": background_image,
        "image_fit": image_fit,
        "image_opacity": image_opacity,
        "ring_enabled": ring_enabled,
        "ring_start": ring_start.upper(),
        "ring_end": ring_end.upper(),
    }


def validate_module_config(payload: Any) -> dict[str, bool]:
    if payload is None:
        return dict(MODULE_DEFAULTS)
    if not isinstance(payload, dict):
        raise ValueError("modules must be an object")
    result: dict[str, bool] = {}
    for module_id, default in MODULE_DEFAULTS.items():
        enabled = payload.get(module_id, default)
        if not isinstance(enabled, bool):
            raise ValueError(f"modules.{module_id} must be a boolean")
        result[module_id] = enabled
    return result


def default_dotii_config() -> dict[str, Any]:
    return {
        "revision": 0,
        "animations": {
            expression_id: {
                "states": list(DOTII_DEFAULT_BUSINESS_ASSIGNMENTS.get(expression_id, ())),
                "state_duration_ms": 0,
            }
            for expression_id in DOTII_EXPRESSION_IDS
        },
        "available_states": [
            dict(definition) for definition in DOTII_BUSINESS_STATE_DEFINITIONS
        ],
        "state_groups": [dict(group) for group in DOTII_STATE_GROUPS],
        "fixed_states": [
            {
                **definition,
                "duration_ms": DOTII_LOOP_DURATIONS_MS[definition["expression"]],
            }
            for definition in DOTII_FIXED_STATE_DEFINITIONS
        ],
        "duration_options": [dict(option) for option in DOTII_STATE_DURATION_OPTIONS],
    }


def validate_dotii_config(payload: Any) -> dict[str, Any]:
    defaults = default_dotii_config()
    if payload is None:
        return defaults
    if not isinstance(payload, dict):
        raise ValueError("dotii config must be an object")
    animations = payload.get("animations")
    if not isinstance(animations, dict):
        raise ValueError("dotii.animations must be an object")

    unknown_animations = set(animations) - DOTII_EXPRESSIONS - DOTII_LEGACY_EXPRESSIONS
    if unknown_animations:
        raise ValueError(f"unsupported Dotii animations: {', '.join(sorted(unknown_animations))}")

    candidates: dict[str, dict[str, Any]] = {}
    assigned_states: dict[str, str] = {}
    for expression_id in (*DOTII_EXPRESSION_IDS, *sorted(DOTII_LEGACY_EXPRESSIONS)):
        candidate = animations.get(expression_id)
        if expression_id in DOTII_LEGACY_EXPRESSIONS and candidate is None:
            continue
        if not isinstance(candidate, dict):
            raise ValueError(f"dotii.animations.{expression_id} must be an object")
        states = candidate.get("states")
        if not isinstance(states, list):
            raise ValueError(f"dotii.animations.{expression_id}.states must be a list")
        candidates[expression_id] = candidate
        for legacy_state_id in states:
            if not isinstance(legacy_state_id, str):
                raise ValueError(f"unsupported Dotii state in {expression_id}")
            expanded_states = DOTII_LEGACY_STATE_EXPANSIONS.get(
                legacy_state_id, (legacy_state_id,)
            )
            for state_id in expanded_states:
                if state_id in DOTII_FIXED_STATE_EXPRESSIONS:
                    continue
                if state_id not in DOTII_BUSINESS_STATE_LABELS:
                    raise ValueError(f"unsupported Dotii state in {expression_id}")
                if state_id in assigned_states:
                    raise ValueError(
                        f"Dotii state {state_id} is assigned to both "
                        f"{assigned_states[state_id]} and {expression_id}"
                    )
                assigned_states[state_id] = expression_id

    validated_animations: dict[str, Any] = {
        expression_id: {"states": [], "state_duration_ms": 0}
        for expression_id in DOTII_EXPRESSION_IDS
    }
    for state_id, source_expression in assigned_states.items():
        expression_id = source_expression if source_expression in DOTII_BUSINESS_EXPRESSION_IDS else (
            DOTII_DEFAULT_BUSINESS_EXPRESSION[state_id]
        )
        validated_animations[expression_id]["states"].append(state_id)

    for expression_id in DOTII_BUSINESS_EXPRESSION_IDS:
        candidate = candidates[expression_id]
        state_duration_ms = candidate.get("state_duration_ms")
        if state_duration_ms is None and "duration_ms" in candidate:
            state_duration_ms = 0
        if state_duration_ms is None:
            state_duration_ms = 0
        if isinstance(state_duration_ms, bool) or not isinstance(state_duration_ms, int):
            raise ValueError(
                f"dotii.animations.{expression_id}.state_duration_ms must be an integer"
            )
        if state_duration_ms not in DOTII_STATE_DURATION_VALUES:
            raise ValueError(
                f"dotii.animations.{expression_id}.state_duration_ms must be "
                "0, 1000, 3000, 5000 or 30000"
            )
        validated_animations[expression_id] = {
            "states": validated_animations[expression_id]["states"],
            "state_duration_ms": state_duration_ms,
        }

    return {
        "revision": _non_negative_int(payload.get("revision", 0), "dotii.revision"),
        "animations": validated_animations,
        "available_states": defaults["available_states"],
        "state_groups": defaults["state_groups"],
        "fixed_states": defaults["fixed_states"],
        "duration_options": defaults["duration_options"],
    }


def _dotii_assignment_for_state(config: dict[str, Any], state_id: str) -> tuple[str, bool]:
    fixed_expression = DOTII_FIXED_STATE_EXPRESSIONS.get(state_id)
    if fixed_expression is not None:
        return fixed_expression, True
    for expression_id in DOTII_BUSINESS_EXPRESSION_IDS:
        if state_id in config["animations"][expression_id]["states"]:
            return expression_id, True
    return "idle_breath", False


def dotii_state(snapshot: dict[str, Any], bambu: dict[str, Any], enabled: bool,
                config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve the device companion expression from public module state only."""
    config = validate_dotii_config(config)
    task = snapshot.get("codex", {}).get("task", {})
    codex_status = task.get("status", "offline")
    bambu_status = bambu.get("status", "offline")
    bambu_connected = bool(bambu.get("configured")) and bool(bambu.get("connected"))

    if codex_status == "failed":
        state_id, reason = "codex_failure", "Codex 任务需要处理"
    elif bambu_connected and bambu_status == "fault":
        state_id, reason = "bambu_failure", "Bambu 打印机需要处理"
    elif codex_status == "offline":
        state_id, reason = "connecting", "正在等待管理中心数据"
    elif codex_status == "working":
        state_id, reason = "codex_working", "Codex 正在工作"
    elif bambu_connected and bambu_status in {"preparing", "printing", "cancelling"}:
        state_id, reason = "bambu_printing", "Bambu 正在打印"
    elif codex_status == "completed":
        state_id, reason = "codex_completed", "Codex 任务已经完成"
    elif bambu_connected and bambu_status == "completed":
        state_id, reason = "bambu_completed", "Bambu 打印已经完成"
    elif codex_status == "waiting_user":
        state_id, reason = "codex_waiting_user", "Codex 正在等待你的操作"
    elif bambu_connected and bambu_status == "paused":
        state_id, reason = "bambu_paused", "Bambu 打印已暂停"
    else:
        state_id, reason = "idle", "当前处于空闲状态"

    expression, state_assigned = _dotii_assignment_for_state(config, state_id)
    animations = config["animations"]

    def local_state(local_id: str) -> dict[str, Any]:
        local_expression = DOTII_FIXED_STATE_EXPRESSIONS[local_id]
        return {
            "expression": local_expression,
            "duration_ms": DOTII_LOOP_DURATIONS_MS[local_expression],
        }

    state_duration_ms = (
        animations[expression]["state_duration_ms"]
        if state_assigned and state_id in DOTII_BUSINESS_STATE_LABELS
        else 0
    )

    return {
        "enabled": enabled,
        "expression": expression,
        "state": state_id,
        "reason": reason,
        "state_assigned": state_assigned,
        "state_duration_ms": state_duration_ms,
        "state_hold": state_assigned and state_duration_ms == 0,
        "state_token": zlib.crc32(state_id.encode("utf-8")) & 0xFFFFFFFF,
        "duration_ms": DOTII_LOOP_DURATIONS_MS[expression],
        "durations_ms": dict(DOTII_LOOP_DURATIONS_MS),
        "local_states": {
            "touch": local_state("touch"),
            "blink": local_state("blink"),
            "long_idle": local_state("long_idle"),
        },
        "config_revision": config["revision"],
        "available_expressions": list(DOTII_EXPRESSION_IDS),
    }


def _validate_timeout_pair(payload: dict[str, Any], screen_off_key: str, sleep_key: str,
                           fallback_screen_off: int, fallback_sleep: int) -> tuple[int, int]:
    if screen_off_key not in payload and sleep_key not in payload:
        return fallback_screen_off, fallback_sleep
    sleep_timeout = payload.get(sleep_key, fallback_sleep)
    if isinstance(sleep_timeout, bool) or not isinstance(sleep_timeout, int):
        raise ValueError(f"display.{sleep_key} must be an integer")
    if sleep_timeout == 5:
        sleep_timeout = 10
    if sleep_timeout not in DISPLAY_TIMEOUT_OPTIONS:
        raise ValueError(f"display.{sleep_key} must be one of 0, 10, 30, 60, 180, 300, 600, 1800")
    screen_off_timeout = payload.get(screen_off_key)
    if screen_off_timeout is None:
        if sleep_timeout == 0:
            screen_off_timeout = fallback_screen_off
        else:
            earlier_options = [value for value in DISPLAY_TIMEOUT_OPTIONS if 0 < value <= sleep_timeout]
            screen_off_timeout = max(earlier_options) if earlier_options else 10
            if not earlier_options:
                sleep_timeout = 10
    if isinstance(screen_off_timeout, bool) or not isinstance(screen_off_timeout, int):
        raise ValueError(f"display.{screen_off_key} must be an integer")
    if screen_off_timeout == 5:
        screen_off_timeout = 10
    if screen_off_timeout not in DISPLAY_TIMEOUT_OPTIONS:
        raise ValueError(f"display.{screen_off_key} must be one of 0, 10, 30, 60, 180, 300, 600, 1800")
    if screen_off_timeout == 0 and sleep_timeout != 0:
        raise ValueError(f"display.{sleep_key} must be never when {screen_off_key} is never")
    if screen_off_timeout != 0 and sleep_timeout != 0 and sleep_timeout < screen_off_timeout:
        raise ValueError(f"display.{sleep_key} must be greater than or equal to {screen_off_key}")
    return screen_off_timeout, sleep_timeout


def validate_display_config(payload: Any) -> dict[str, Any]:
    if payload is None:
        return dict(DISPLAY_DEFAULTS)
    if not isinstance(payload, dict):
        raise ValueError("display must be an object")
    codex_ui = payload.get("codex_ui", DISPLAY_DEFAULTS["codex_ui"])
    if codex_ui not in CODEX_UI_OPTIONS:
        raise ValueError("display.codex_ui must be classic or dual_limit")
    angle = payload.get("docked_rotation_tenths", DISPLAY_DEFAULTS["docked_rotation_tenths"])
    if isinstance(angle, bool) or not isinstance(angle, int) or not 800 <= angle <= 1000:
        raise ValueError("display.docked_rotation_tenths must be an integer between 800 and 1000")
    screen_off_page = payload.get("screen_off_page", DISPLAY_DEFAULTS["screen_off_page"])
    if screen_off_page not in SCREEN_OFF_PAGE_OPTIONS:
        raise ValueError("display.screen_off_page must be none, custom or dotii")
    screen_off_timeout, sleep_timeout = _validate_timeout_pair(
        payload, "screen_off_timeout_seconds", "sleep_timeout_seconds",
        DISPLAY_DEFAULTS["screen_off_timeout_seconds"], DISPLAY_DEFAULTS["sleep_timeout_seconds"],
    )
    charging_screen_off_timeout, charging_sleep_timeout = _validate_timeout_pair(
        payload, "charging_screen_off_timeout_seconds", "charging_sleep_timeout_seconds",
        screen_off_timeout, sleep_timeout,
    )
    return {
        "revision": _non_negative_int(payload.get("revision", 0), "display.revision"),
        "codex_ui": codex_ui,
        "docked_rotation_tenths": angle,
        "screen_off_timeout_seconds": screen_off_timeout,
        "sleep_timeout_seconds": sleep_timeout,
        "charging_screen_off_timeout_seconds": charging_screen_off_timeout,
        "charging_sleep_timeout_seconds": charging_sleep_timeout,
        "screen_off_page": screen_off_page,
    }


def validate_snapshot(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("schema_version must be 1")
    codex = payload.get("codex")
    task = codex.get("task") if isinstance(codex, dict) else None
    if not isinstance(codex, dict) or not isinstance(task, dict):
        raise ValueError("codex.task is required")
    weekly = _non_negative_int(codex.get("weekly_remaining_percent"), "weekly_remaining_percent")
    if weekly > 100:
        raise ValueError("weekly_remaining_percent must be between 0 and 100")
    five_hour = _non_negative_int(
        codex.get("five_hour_remaining_percent", 0), "five_hour_remaining_percent"
    )
    if five_hour > 100:
        raise ValueError("five_hour_remaining_percent must be between 0 and 100")
    raw_tasks = codex.get("tasks")
    if raw_tasks is not None and not isinstance(raw_tasks, list):
        raise ValueError("codex.tasks must be a list")
    ordered_raw: list[dict[str, Any]] = []
    seen_thread_ids: set[str] = set()
    for item in [task, *(raw_tasks or [])]:
        if not isinstance(item, dict):
            raise ValueError("codex.tasks entries must be objects")
        thread_id = str(item.get("thread_id") or "")
        identity = thread_id or f"anonymous-{len(ordered_raw)}"
        if identity in seen_thread_ids:
            continue
        seen_thread_ids.add(identity)
        ordered_raw.append(item)
    ordered_raw.sort(key=lambda item: (
        _non_negative_int(item.get("started_at_epoch", 0), "started_at_epoch"),
        str(item.get("thread_id") or ""),
    ))
    ordered_raw = ordered_raw[:MAX_DISPLAY_TASKS]
    primary_task = _validate_codex_task(ordered_raw[0])
    tasks = [primary_task]
    tasks.extend(_validate_codex_task(item) for item in ordered_raw[1:])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_epoch": _non_negative_int(
            payload.get("generated_at_epoch", int(time.time())), "generated_at_epoch"
        ),
        "preview_data": bool(payload.get("preview_data", False)),
        "source": _bounded_text(payload.get("source", "manual"), 32, "source"),
        "codex": {
            "five_hour_available": bool(codex.get("five_hour_available", False)),
            "five_hour_remaining_percent": five_hour,
            "five_hour_reset_date": _bounded_text(
                codex.get("five_hour_reset_date", ""), 15, "five_hour_reset_date"
            ),
            "weekly_available": bool(codex.get("weekly_available", True)),
            "weekly_remaining_percent": weekly,
            "weekly_used_percent": _non_negative_int(
                codex.get("weekly_used_percent", 100 - weekly), "weekly_used_percent"
            ),
            "weekly_tokens_available": bool(codex.get(
                "weekly_tokens_available", codex.get("today_tokens_available", False)
            )),
            "weekly_tokens": _non_negative_int(
                codex.get("weekly_tokens", codex.get("today_tokens", 0)), "weekly_tokens"
            ),
            "weekly_tokens_as_of": _bounded_text(
                codex.get("weekly_tokens_as_of", ""), 5, "weekly_tokens_as_of"
            ),
            "reset_date": _bounded_text(codex.get("reset_date", ""), 15, "reset_date"),
            "plan_type": _bounded_text(codex.get("plan_type", ""), 31, "plan_type"),
            "rate_limit_reached": _bounded_text(
                codex.get("rate_limit_reached", ""), 31, "rate_limit_reached"
            ),
            "task": primary_task,
            "tasks": tasks,
        },
    }


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()

    def read(self) -> dict[str, Any]:
        with self.lock:
            return validate_snapshot(json.loads(self.path.read_text(encoding="utf-8")))

    def write(self, snapshot: dict[str, Any]) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(prefix="state-", suffix=".json", dir=self.path.parent)
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(snapshot, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                os.replace(temporary_name, self.path)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)


class CustomConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()

    def read(self) -> dict[str, Any]:
        with self.lock:
            if not self.path.is_file():
                return dict(CUSTOM_DEFAULTS)
            return validate_custom_config(json.loads(self.path.read_text(encoding="utf-8")))

    def write(self, config: dict[str, Any]) -> dict[str, Any]:
        validated = validate_custom_config(config)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(prefix="custom-", suffix=".json", dir=self.path.parent)
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(validated, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                os.replace(temporary_name, self.path)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        return validated


class CustomAssetStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.render_path = folder / "custom-screen.rgb565"
        self.pending_path = folder / "custom-screen.pending"
        self.source_path = folder / "custom-source.bin"
        self.source_meta_path = folder / "custom-source.json"
        self.lock = threading.RLock()

    @staticmethod
    def _atomic_bytes(path: Path, body: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(body)
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def write_source(self, body: bytes, content_type: str) -> dict[str, Any]:
        if content_type == "image/png":
            if not body.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("PNG 文件签名无效")
        elif content_type == "image/jpeg":
            if len(body) < 4 or not body.startswith(b"\xff\xd8") or not body.endswith(b"\xff\xd9"):
                raise ValueError("JPEG 文件签名无效")
        else:
            raise ValueError("仅支持 PNG 或 JPEG 图片")
        with self.lock:
            self._atomic_bytes(self.source_path, body)
            self._atomic_bytes(
                self.source_meta_path,
                json.dumps({
                    "content_type": content_type,
                    "revision": zlib.crc32(body) & 0xFFFFFFFF,
                }, ensure_ascii=False).encode("utf-8"),
            )
            return self.source_info()

    def source_info(self) -> dict[str, Any]:
        with self.lock:
            if not self.source_path.is_file() or not self.source_meta_path.is_file():
                return {"source_available": False, "source_revision": 0, "source_mime": ""}
            meta = json.loads(self.source_meta_path.read_text(encoding="utf-8"))
            return {
                "source_available": True,
                "source_revision": int(meta.get("revision", 0)),
                "source_mime": meta.get("content_type", "application/octet-stream"),
            }

    def read_source(self) -> tuple[bytes, str]:
        info = self.source_info()
        if not info["source_available"]:
            raise FileNotFoundError("custom source image not found")
        return self.source_path.read_bytes(), info["source_mime"]

    def stage_render(self, body: bytes) -> str:
        if len(body) != CUSTOM_FRAME_SIZE:
            raise ValueError(f"自定义画面必须为 {CUSTOM_FRAME_SIZE} 字节 RGB565")
        token = hashlib.sha256(body).hexdigest()
        with self.lock:
            self._atomic_bytes(self.pending_path, body)
        return token

    def commit_render(self, token: str) -> dict[str, Any]:
        if not isinstance(token, str) or len(token) != 64:
            raise ValueError("render_token 无效")
        with self.lock:
            if not self.pending_path.is_file():
                raise ValueError("待保存画面不存在，请重新生成")
            body = self.pending_path.read_bytes()
            if not secrets.compare_digest(hashlib.sha256(body).hexdigest(), token):
                raise ValueError("待保存画面校验失败")
            os.replace(self.pending_path, self.render_path)
            return self.render_info()

    def render_info(self) -> dict[str, Any]:
        with self.lock:
            if not self.render_path.is_file() or self.render_path.stat().st_size != CUSTOM_FRAME_SIZE:
                return {"image_available": False, "image_revision": 0, "image_size": 0}
            body = self.render_path.read_bytes()
            return {
                "image_available": True,
                "image_revision": zlib.crc32(body) & 0xFFFFFFFF,
                "image_size": len(body),
            }

    def read_render(self) -> bytes:
        with self.lock:
            body = self.render_path.read_bytes()
            if len(body) != CUSTOM_FRAME_SIZE:
                raise ValueError("stored custom frame has invalid size")
            return body


class ModuleConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()

    def read(self) -> dict[str, bool]:
        with self.lock:
            if not self.path.is_file():
                return dict(MODULE_DEFAULTS)
            return validate_module_config(json.loads(self.path.read_text(encoding="utf-8")))

    def write(self, config: dict[str, Any]) -> dict[str, bool]:
        validated = validate_module_config(config)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(prefix="modules-", suffix=".json", dir=self.path.parent)
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(validated, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                os.replace(temporary_name, self.path)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        return validated


class DotiiConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()

    def read(self) -> dict[str, Any]:
        with self.lock:
            if not self.path.is_file():
                return default_dotii_config()
            return validate_dotii_config(json.loads(self.path.read_text(encoding="utf-8")))

    def write(self, config: dict[str, Any]) -> dict[str, Any]:
        validated = validate_dotii_config(config)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(prefix="dotii-", suffix=".json", dir=self.path.parent)
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(validated, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                os.replace(temporary_name, self.path)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        return validated


class DisplayConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()

    def read(self) -> dict[str, Any]:
        with self.lock:
            if not self.path.is_file():
                return dict(DISPLAY_DEFAULTS)
            return validate_display_config(json.loads(self.path.read_text(encoding="utf-8")))

    def write(self, config: dict[str, Any]) -> dict[str, Any]:
        validated = validate_display_config(config)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(prefix="display-", suffix=".json", dir=self.path.parent)
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(validated, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                os.replace(temporary_name, self.path)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        return validated


class RuntimeStatus:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.started_at_epoch = int(time.time())
        self.collector_state = "disabled"
        self.collector_detail = "Codex 模块已关闭"
        self.collector_updated_epoch = self.started_at_epoch

    def report(self, state: str, detail: str) -> None:
        with self.lock:
            self.collector_state = state
            self.collector_detail = detail[:240]
            self.collector_updated_epoch = int(time.time())

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "started_at_epoch": self.started_at_epoch,
                "collector_state": self.collector_state,
                "collector_detail": self.collector_detail,
                "collector_updated_epoch": self.collector_updated_epoch,
            }




class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "StateDisplayBridge/1.0"

    @property
    def bridge(self) -> "BridgeServer":
        return self.server  # type: ignore[return-value]

    def _authorized(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Bridge-Token", ""), self.bridge.token)

    def _local_admin(self) -> bool:
        return _is_loopback(self.client_address[0])

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _deny_nonlocal(self) -> bool:
        if self._local_admin():
            return False
        self._send_json(HTTPStatus.FORBIDDEN, {"error": "management interface is local only"})
        return True

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            raise ValueError("invalid request size")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _read_bytes(self, maximum: int, *, exact: int | None = None) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > maximum or (exact is not None and length != exact):
            raise ValueError("invalid request size")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("incomplete request body")
        return body

    def _read_admin_action(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("management actions require application/json")
        payload = self._read_json()
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        return payload

    def _serve_static(self, request_path: str) -> None:
        if self._deny_nonlocal():
            return
        relative = "index.html" if request_path == "/" else unquote(request_path).lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        try:
            target.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not target.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "image/svg+xml"}:
            content_type += "; charset=utf-8"
        self._send_bytes(HTTPStatus.OK, target.read_bytes(), content_type)

    def _overview(self) -> dict[str, Any]:
        snapshot = self.bridge.snapshot()
        address = local_ipv4()
        runtime = self.bridge.runtime.snapshot()
        return {
            "app": {"name": "Dotii 管理中心", "version": "0.9"},
            "bridge": {
                "online": True,
                "local_url": f"http://127.0.0.1:{self.bridge.server_port}",
                "device_url": f"http://{address}:{self.bridge.server_port}/api/v1/snapshot",
                "token": self.bridge.token,
                "port": self.bridge.server_port,
                "auto_start": startup_enabled(),
                **runtime,
            },
            "snapshot": snapshot,
            "dotii_config": self.bridge.dotii.read(),
            "display_config": self.bridge.display.read(),
            "bambu_config": self.bridge.bambu_config.public(),
            "bluetooth": self.bridge.bluetooth.snapshot(),
            "firmware": self.bridge.firmware.snapshot(),
            "modules": [
                {"id": "codex", "name": "Codex", "enabled": snapshot["modules"]["codex"], "available": True, "locked": False},
                {"id": "bambu", "name": "Bambu", "enabled": snapshot["modules"]["bambu"], "available": True, "locked": False},
                {"id": "custom", "name": "自定义", "enabled": snapshot["custom"]["enabled"], "available": True, "locked": False},
                {"id": "dotii", "name": "Dotii", "enabled": snapshot["modules"]["dotii"], "available": True, "locked": False},
            ],
        }

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True, "schema_version": SCHEMA_VERSION})
        elif path == "/api/v1/snapshot":
            if not self._authorized():
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid bridge token"})
                return
            try:
                self._send_json(HTTPStatus.OK, self.bridge.snapshot())
            except (OSError, ValueError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})
        elif path == "/api/v1/admin/overview":
            if self._deny_nonlocal():
                return
            try:
                self._send_json(HTTPStatus.OK, self._overview())
            except (OSError, ValueError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})
        elif path == "/api/v1/admin/custom/source":
            if self._deny_nonlocal():
                return
            try:
                body, content_type = self.bridge.assets.read_source()
                self._send_bytes(HTTPStatus.OK, body, content_type)
            except FileNotFoundError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "尚未上传背景图片"})
            except (OSError, ValueError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})
        elif path == "/api/v1/admin/bambu/camera.jpg":
            if self._deny_nonlocal():
                return
            body = self.bridge.bambu.get_camera_jpeg()
            if body:
                self._send_bytes(HTTPStatus.OK, body, "image/jpeg")
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "打印机相机暂不可用"})
        elif path == "/api/v1/bambu-camera.rgb565":
            if not self._authorized():
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid bridge token"})
                return
            body = self.bridge.bambu.get_camera_rgb565()
            if body:
                self._send_bytes(HTTPStatus.OK, body, "application/octet-stream")
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "camera unavailable"})
        elif path == "/api/v1/custom-screen.rgb565":
            if not self._authorized():
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid bridge token"})
                return
            try:
                self._send_bytes(HTTPStatus.OK, self.bridge.assets.read_render(), "application/octet-stream")
            except (OSError, ValueError) as error:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(error)})
        else:
            self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/v1/admin/codex/check":
            if self._deny_nonlocal():
                return
            try:
                self._read_admin_action()
                if not self.bridge.modules.read()["codex"]:
                    raise ValueError("请先启用 Codex 功能再运行检测")
                result = probe_app_server(
                    self.bridge.runtime_folder,
                    self.bridge.application_folder,
                    self.bridge.codex_command,
                )
                result["collector"] = self.bridge.runtime.snapshot()
                self._send_json(HTTPStatus.OK, result)
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/bluetooth/install":
            if self._deny_nonlocal():
                return
            try:
                self._read_admin_action()
                started = self.bridge.bluetooth.start_install()
                status = HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT
                self._send_json(status, {"ok": started, "bluetooth": self.bridge.bluetooth.snapshot()})
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/bluetooth/scan":
            if self._deny_nonlocal():
                return
            try:
                self._read_admin_action()
                started = self.bridge.bluetooth.start_scan()
                status = HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT
                self._send_json(status, {"ok": started})
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/bluetooth/configure":
            if self._deny_nonlocal():
                return
            try:
                payload = self._read_admin_action()
                address = local_ipv4()
                bridge_url = f"http://{address}:{self.bridge.server_port}/api/v1/snapshot"
                started = self.bridge.bluetooth.start_configure(
                    address=payload.get("address"), ssid=payload.get("ssid"),
                    password=payload.get("password"), bridge_url=bridge_url,
                    bridge_token=self.bridge.token,
                )
                status = HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT
                self._send_json(status, {"ok": started})
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/firmware/refresh":
            if self._deny_nonlocal():
                return
            try:
                self._read_admin_action()
                self.bridge.firmware.refresh()
                self._send_json(HTTPStatus.OK, {"ok": True, "firmware": self.bridge.firmware.snapshot()})
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/firmware/flash":
            if self._deny_nonlocal():
                return
            try:
                payload = self._read_admin_action()
                if payload.get("confirmation") != "FLASH_DOTII":
                    raise ValueError("需要确认后才能烧录")
                started = self.bridge.firmware.start_flash(payload.get("port"))
                if not started:
                    self._send_json(HTTPStatus.CONFLICT, {"error": "已有烧录任务正在运行"})
                else:
                    self._send_json(HTTPStatus.ACCEPTED, {"ok": True})
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/bambu/config":
            if self._deny_nonlocal():
                return
            try:
                config = self.bridge.bambu_config.write(self._read_admin_action(), preserve_secret=True)
                self.bridge.bambu.reconfigure()
                self._send_json(HTTPStatus.OK, {"ok": True, "bambu_config": config})
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/bambu/refresh":
            if self._deny_nonlocal():
                return
            try:
                self._read_admin_action()
                self.bridge.bambu.reconfigure()
                self._send_json(HTTPStatus.ACCEPTED, {"ok": True})
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path in {"/api/v1/admin/bambu/command", "/api/v1/bambu/command"}:
            if path.startswith("/api/v1/admin/"):
                if self._deny_nonlocal():
                    return
            elif not self._authorized():
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid bridge token"})
                return
            try:
                payload = self._read_admin_action()
                result = self.bridge.bambu.command(payload.get("action"))
                self._send_json(HTTPStatus.ACCEPTED, {"ok": True, **result})
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/custom/source":
            if self._deny_nonlocal():
                return
            try:
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                info = self.bridge.assets.write_source(
                    self._read_bytes(MAX_CUSTOM_SOURCE_SIZE), content_type
                )
                self._send_json(HTTPStatus.OK, {"ok": True, **info})
            except (OSError, ValueError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/custom/render":
            if self._deny_nonlocal():
                return
            try:
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/octet-stream":
                    raise ValueError("自定义画面必须使用 application/octet-stream")
                token = self.bridge.assets.stage_render(
                    self._read_bytes(CUSTOM_FRAME_SIZE, exact=CUSTOM_FRAME_SIZE)
                )
                self._send_json(HTTPStatus.OK, {"ok": True, "render_token": token})
            except (OSError, ValueError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/custom":
            if self._deny_nonlocal():
                return
            try:
                payload = self._read_admin_action()
                current = self.bridge.custom.read()
                modules = self.bridge.modules.read()
                if payload.get("enabled") is False and not any(modules.values()):
                    raise ValueError("至少保留一个启用页面")
                payload["revision"] = current["revision"] + 1
                render_token = payload.pop("render_token", None)
                validated = validate_custom_config(payload)
                if render_token is not None:
                    self.bridge.assets.commit_render(render_token)
                custom = self.bridge.custom.write(validated)
                custom.update(self.bridge.assets.source_info())
                custom.update(self.bridge.assets.render_info())
                self._send_json(HTTPStatus.OK, {"ok": True, "custom": custom})
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/modules":
            if self._deny_nonlocal():
                return
            try:
                payload = self._read_admin_action()
                module_id = payload.get("id")
                enabled = payload.get("enabled")
                if module_id not in MODULE_DEFAULTS or not isinstance(enabled, bool):
                    raise ValueError("id must be codex, bambu or dotii and enabled must be boolean")
                modules = self.bridge.modules.read()
                candidate = {**modules, module_id: enabled}
                if not any(candidate.values()) and not self.bridge.custom.read()["enabled"]:
                    raise ValueError("至少保留一个启用页面")
                saved = self.bridge.modules.write(candidate)
                if module_id == "codex":
                    self.bridge.runtime.report("starting", "正在重新连接 Codex App Server")
                    self.bridge.collector_restart.set()
                    if not enabled:
                        self.bridge.runtime.report("disabled", "Codex 模块已关闭")
                elif module_id == "bambu":
                    self.bridge.bambu.set_enabled(enabled)
                self._send_json(HTTPStatus.OK, {"ok": True, "modules": saved})
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/dotii":
            if self._deny_nonlocal():
                return
            try:
                payload = self._read_admin_action()
                current = self.bridge.dotii.read()
                candidate = {**payload, "revision": current["revision"] + 1}
                config = self.bridge.dotii.write(candidate)
                self._send_json(HTTPStatus.OK, {"ok": True, "dotii_config": config})
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/display":
            if self._deny_nonlocal():
                return
            try:
                payload = self._read_admin_action()
                current = self.bridge.display.read()
                candidate = {**current, **payload, "revision": current["revision"] + 1}
                display = self.bridge.display.write(candidate)
                self._send_json(HTTPStatus.OK, {"ok": True, "display_config": display})
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path == "/api/v1/admin/settings":
            if self._deny_nonlocal():
                return
            try:
                payload = self._read_json()
                if not isinstance(payload, dict) or not isinstance(payload.get("auto_start"), bool):
                    raise ValueError("auto_start must be a boolean")
                enabled = set_startup(payload["auto_start"])
                self._send_json(HTTPStatus.OK, {"ok": True, "auto_start": enabled})
            except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        if path != "/api/v1/snapshot":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._authorized():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid bridge token"})
            return
        try:
            snapshot = validate_snapshot(self._read_json())
            self.bridge.store.write(snapshot)
            self._send_json(HTTPStatus.OK, {"ok": True, "generated_at_epoch": snapshot["generated_at_epoch"]})
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def log_message(self, format_string: str, *args: Any) -> None:
        logging.info("%s %s", self.client_address[0], format_string % args)


class BridgeServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        store: StateStore,
        token: str,
        runtime: RuntimeStatus,
        collector_restart: threading.Event,
        custom: CustomConfigStore,
        modules: ModuleConfigStore,
        dotii: DotiiConfigStore,
        display: DisplayConfigStore,
        assets: CustomAssetStore,
        bambu_config: BambuConfigStore,
        bambu: BambuService,
        bluetooth: BluetoothBridge,
        firmware: FirmwareFlasher,
        runtime_folder: Path,
        application_folder: Path,
        codex_command: str | None,
    ) -> None:
        super().__init__(address, BridgeHandler)
        self.store = store
        self.token = token
        self.runtime = runtime
        self.collector_restart = collector_restart
        self.custom = custom
        self.modules = modules
        self.dotii = dotii
        self.display = display
        self.assets = assets
        self.bambu_config = bambu_config
        self.bambu = bambu
        self.bluetooth = bluetooth
        self.firmware = firmware
        self.runtime_folder = runtime_folder
        self.application_folder = application_folder
        self.codex_command = codex_command

    def snapshot(self) -> dict[str, Any]:
        snapshot = self.store.read()
        custom = self.custom.read()
        custom.update(self.assets.source_info())
        custom.update(self.assets.render_info())
        snapshot["custom"] = custom
        snapshot["bambu"] = self.bambu.snapshot()
        modules = self.modules.read()
        snapshot["modules"] = {**modules, "custom": custom["enabled"]}
        snapshot["dotii"] = dotii_state(
            snapshot, snapshot["bambu"], modules["dotii"], self.dotii.read()
        )
        snapshot["display"] = self.display.read()
        return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="LAN bridge for the Dotii status display")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--token", default=os.environ.get("STATE_DISPLAY_BRIDGE_TOKEN"))
    parser.add_argument("--app-server", action="store_true", help="read account and task state through Codex app-server")
    parser.add_argument("--codex-command", help="path to a standalone Codex CLI executable or codex.js")
    parser.add_argument("--app-server-interval", type=float, default=2.0)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Preserve the user's enabled/disabled choice while repairing stale paths
    # and releases that incorrectly registered DotiiBridge.exe as the login app.
    try:
        if startup_enabled():
            set_startup(True)
    except OSError as error:
        logging.warning("无法修复登录自启动命令：%s", error)
    local_app_data = os.environ.get("LOCALAPPDATA")
    runtime_folder = (Path(local_app_data) if local_app_data else Path.home() / ".state-display") / "StateDisplay"
    runtime_folder.mkdir(parents=True, exist_ok=True)
    state_path = arguments.state or runtime_folder / "state.json"
    if not state_path.exists():
        shutil.copyfile(resource_path("state.json"), state_path)

    token = arguments.token or load_or_create_token(runtime_folder / "bridge-token")
    store = StateStore(state_path)
    runtime = RuntimeStatus()
    stop = threading.Event()
    collector_restart = threading.Event()
    custom = CustomConfigStore(runtime_folder / "custom.json")
    modules = ModuleConfigStore(runtime_folder / "modules.json")
    dotii = DotiiConfigStore(runtime_folder / "dotii.json")
    display = DisplayConfigStore(runtime_folder / "display.json")
    assets = CustomAssetStore(runtime_folder)
    bambu_config = BambuConfigStore(runtime_folder / "bambu.json")
    bambu = BambuService(
        bambu_config,
        lambda: modules.read()["bambu"],
        lambda: resolve_ffmpeg(runtime_folder),
    )
    bambu.start()
    bluetooth = BluetoothBridge(runtime_folder)
    bluetooth.start()
    firmware = FirmwareFlasher(project_root(), runtime_folder)
    server = BridgeServer((arguments.host, arguments.port), store, token, runtime, collector_restart,
                          custom, modules, dotii, display, assets, bambu_config, bambu,
                          bluetooth, firmware, runtime_folder, application_root(), arguments.codex_command)
    if arguments.app_server:
        threading.Thread(
            target=run_collector,
            kwargs={
                "runtime_folder": runtime_folder,
                "cwd": application_root(),
                "interval": max(1.0, arguments.app_server_interval),
                "stop": stop,
                "publish": lambda snapshot: store.write(validate_snapshot(snapshot)),
                "codex_command": arguments.codex_command,
                "report": runtime.report,
                "restart": collector_restart,
                "enabled": lambda: modules.read()["codex"],
            },
            name="codex-collector",
            daemon=True,
        ).start()
    else:
        runtime.report("disabled", "自动数据源未启用")

    address = local_ipv4()
    logging.info("Dotii 管理中心：http://127.0.0.1:%s", arguments.port)
    logging.info("ESP32 地址：http://%s:%s/api/v1/snapshot", address, arguments.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        bambu.stop()
        bluetooth.stop()
        firmware.stop()
        server.server_close()


if __name__ == "__main__":
    if is_frozen() and len(sys.argv) > 1 and sys.argv[1] == "--esptool":
        import esptool

        sys.argv = ["esptool", *sys.argv[2:]]
        raise SystemExit(esptool.main())
    main()
