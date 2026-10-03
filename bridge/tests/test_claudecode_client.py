from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from claudecode_client import (  # noqa: E402
    HOOK_EVENTS,
    PERMISSION_MAX_PENDING,
    PERMISSION_WAIT_SECONDS,
    ClaudeCodeMonitor,
    PermissionBroker,
    hook_command,
    permission_hook_command,
    update_hooks,
)


def event(name, session="session-1", cwd="/Users/dev/workplace/Dotii-Display", **extra):
    return {"hook_event_name": name, "session_id": session, "cwd": cwd, **extra}


class ClaudeCodeEventMappingTests(unittest.TestCase):
    def test_lifecycle_events_map_to_statuses(self) -> None:
        monitor = ClaudeCodeMonitor()
        cases = {
            "UserPromptSubmit": "working",
            "PostToolUse": "working",
            "PostToolUseFailure": "working",
            "Stop": "completed",
            "StopFailure": "failed",
            "SessionStart": "idle",
        }
        for name, expected in cases.items():
            self.assertEqual(monitor.record_event(event(name)), expected, msg=name)

    def test_notification_maps_to_waiting_user_only_for_attention(self) -> None:
        monitor = ClaudeCodeMonitor()
        self.assertEqual(
            monitor.record_event(event("Notification", message="permission_prompt: allow Bash")),
            "waiting_user",
        )
        self.assertIsNone(
            monitor.record_event(event("Notification", message="Task completed")),
        )

    def test_unknown_events_and_missing_session_are_ignored(self) -> None:
        monitor = ClaudeCodeMonitor()
        self.assertIsNone(monitor.record_event(event("PreToolUse")))
        self.assertIsNone(monitor.record_event({"hook_event_name": "Stop"}))
        self.assertEqual(monitor.snapshot()["session_count"], 0)

    def test_session_end_removes_the_session(self) -> None:
        monitor = ClaudeCodeMonitor()
        monitor.record_event(event("UserPromptSubmit"))
        self.assertEqual(monitor.snapshot()["session_count"], 1)
        monitor.record_event(event("SessionEnd"))
        snapshot = monitor.snapshot()
        self.assertEqual(snapshot["session_count"], 0)
        self.assertEqual(snapshot["service_state"], "idle")

    def test_latest_session_wins_as_primary(self) -> None:
        monitor = ClaudeCodeMonitor()
        monitor.record_event(event("Stop", session="a", cwd="/work/alpha"), now=1000.0)
        monitor.record_event(event("UserPromptSubmit", session="b", cwd="/work/beta"), now=1100.0)
        snapshot = monitor.snapshot(now=1200.0)
        self.assertEqual(snapshot["status"], "working")
        self.assertEqual(snapshot["project"], "beta")
        self.assertEqual(snapshot["session_count"], 2)

    def test_session_cap_evicts_oldest(self) -> None:
        monitor = ClaudeCodeMonitor()
        for index in range(20):
            monitor.record_event(
                event("SessionStart", session=f"s{index}"), now=1000.0 + index,
            )
        snapshot = monitor.snapshot(now=1100.0)
        self.assertEqual(snapshot["session_count"], 16)
        self.assertEqual(snapshot["project"], "Dotii-Display")

    def test_module_disabled_reports_disabled_and_clears(self) -> None:
        monitor = ClaudeCodeMonitor()
        monitor.record_event(event("UserPromptSubmit"))
        self.assertEqual(monitor.snapshot(module_enabled=False)["service_state"], "disabled")
        monitor.set_enabled(False)
        self.assertEqual(monitor.snapshot()["session_count"], 0)

    def test_stale_sessions_expire_after_a_day(self) -> None:
        monitor = ClaudeCodeMonitor()
        monitor.record_event(event("Stop"), now=1000.0)
        self.assertEqual(monitor.snapshot(now=2000.0)["updated_at_epoch"], 1000)
        monitor.record_event(event("Stop"), now=1000.0)
        self.assertEqual(monitor.snapshot(now=1000.0 + 25 * 3600)["session_count"], 0)

    def test_project_name_is_bounded(self) -> None:
        monitor = ClaudeCodeMonitor()
        monitor.record_event(event("Stop", cwd="/work/" + "很" * 80))
        project = monitor.snapshot()["project"]
        self.assertLessEqual(len(project.encode("utf-8")), 47)


class ClaudeCodeHooksTests(unittest.TestCase):
    @staticmethod
    def _settings(temporary: str) -> Path:
        return Path(temporary) / "settings.json"

    @staticmethod
    def _commands(settings: dict, event: str) -> list[str]:
        return [
            str(hook.get("command", ""))
            for group in settings["hooks"].get(event, [])
            if isinstance(group, dict)
            for hook in group.get("hooks", [])
            if isinstance(hook, dict)
        ]

    def test_install_adds_all_events_in_official_nested_shape(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            path = self._settings(temporary)
            first = update_hooks(path, 8787)
            self.assertEqual(sorted(first["changed"]), sorted([*HOOK_EVENTS, "PermissionRequest"]))
            settings = json.loads(path.read_text(encoding="utf-8"))
            group = settings["hooks"]["Stop"][0]
            self.assertEqual(group.get("matcher"), "")
            self.assertEqual(group["hooks"][0]["type"], "command")
            second = update_hooks(path, 8787)
            self.assertEqual(second["changed"], [])
            self.assertEqual(len(settings["hooks"]["Stop"]), 1)

    def test_install_preserves_user_hooks_and_removes_only_ours(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            path = self._settings(temporary)
            path.write_text(json.dumps({
                "model": "opus",
                "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "echo mine"}]}]},
            }), encoding="utf-8")
            update_hooks(path, 8787)
            settings = json.loads(path.read_text(encoding="utf-8"))
            commands = self._commands(settings, "Stop")
            self.assertEqual(sorted(commands), sorted(["echo mine", hook_command(8787)]))
            removed = update_hooks(path, 8787, "remove")
            self.assertIn("Stop", removed["changed"])
            settings = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(self._commands(settings, "Stop"), ["echo mine"])
            self.assertNotIn("SessionStart", settings["hooks"])

    def test_install_replaces_stale_port_command(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            path = self._settings(temporary)
            update_hooks(path, 8787)
            update_hooks(path, 9000)
            settings = json.loads(path.read_text(encoding="utf-8"))
            commands = self._commands(settings, "Stop")
            self.assertEqual(len(commands), 1)
            self.assertIn(":9000", commands[0])

    def test_broken_settings_file_is_rejected_untouched(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            path = self._settings(temporary)
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                update_hooks(path, 8787)
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_backup_keeps_the_earliest_original_state(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            path = self._settings(temporary)
            path.write_text('{"model": "opus"}', encoding="utf-8")
            update_hooks(path, 8787)
            backup = path.with_name(path.name + ".dotii-backup")
            self.assertEqual(backup.read_text(encoding="utf-8"), '{"model": "opus"}')
            update_hooks(path, 8787, "remove")
            # remove 也不覆盖最初备份
            self.assertEqual(backup.read_text(encoding="utf-8"), '{"model": "opus"}')

    def test_hook_command_targets_loopback_with_timeout(self) -> None:
        command = hook_command(8787)
        self.assertIn("127.0.0.1:8787/api/v1/admin/claudecode/event", command)
        self.assertIn("-m 2", command)
        # 跨平台：不得依赖 sh 外壳或单引号语法（Windows cmd 无法执行）
        self.assertNotIn("sh -c", command)
        self.assertNotIn("'", command)
        # 服务离线（重启间隙等）时静默成功，不触发 Claude Code 的 hook 错误提示
        self.assertTrue(command.endswith("|| exit 0"))

    def test_permission_hook_command_waits_long_and_silently_degrades(self) -> None:
        command = permission_hook_command(8787)
        self.assertIn("127.0.0.1:8787/api/v1/admin/claudecode/permission", command)
        # 决策端点 hold 到 300s，curl 超时留余量；离线时静默回落本机提示
        self.assertIn("-m 310", command)
        self.assertNotIn("'", command)
        self.assertTrue(command.endswith("|| exit 0"))

    def test_install_adds_permission_request_with_decision_command(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            path = self._settings(temporary)
            update_hooks(path, 8787)
            settings = json.loads(path.read_text(encoding="utf-8"))
            commands = self._commands(settings, "PermissionRequest")
            self.assertEqual(commands, [permission_hook_command(8787)])
            removed = update_hooks(path, 8787, "remove")
            self.assertIn("PermissionRequest", removed["changed"])
            settings = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("PermissionRequest", settings["hooks"])


class PermissionBrokerTests(unittest.TestCase):
    @staticmethod
    def _submit(broker: PermissionBroker, tool: str, **extra) -> dict:
        return {"tool_name": tool, "session_id": "s1",
                "cwd": "/Users/dev/workplace/proj", **extra}

    def _submit_in_thread(self, broker: PermissionBroker, payload: dict, timeout: float = 5.0,
                          wait_count: int = 1):
        result: dict = {}

        def worker() -> None:
            result["decision"] = broker.submit_and_wait(payload, timeout=timeout)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        deadline = time.time() + 2.0
        while len(broker.snapshot()["queue"]) < wait_count and time.time() < deadline:
            time.sleep(0.01)
        return thread, result

    def test_mode_off_returns_immediately_without_queueing(self) -> None:
        broker = PermissionBroker()
        decision = broker.submit_and_wait(self._submit(broker, "Bash", tool_input={"command": "ls"}))
        self.assertIsNone(decision)
        self.assertFalse(broker.snapshot()["enabled"])
        self.assertIsNone(broker.snapshot()["pending"])

    def test_resolve_allows_and_denies_and_clears_queue(self) -> None:
        broker = PermissionBroker()
        broker.set_mode(True)
        thread, result = self._submit_in_thread(
            broker, self._submit(broker, "Bash", tool_input={"command": "git push"}))
        snapshot = broker.snapshot()
        pending = snapshot["pending"]
        self.assertEqual(pending["tool"], "Bash")
        self.assertEqual(pending["preview"], "git push")
        self.assertEqual(pending["project"], "proj")
        self.assertEqual(pending["queued"], 0)

        self.assertTrue(broker.resolve(pending["id"], True))
        thread.join(2.0)
        self.assertEqual(result["decision"], {"behavior": "allow"})
        self.assertIsNone(broker.snapshot()["pending"])

    def test_timeout_returns_none_and_leaves_fail_open(self) -> None:
        broker = PermissionBroker()
        broker.set_mode(True)
        thread, result = self._submit_in_thread(
            broker, self._submit(broker, "Bash", tool_input={"command": "ls"}), timeout=0.05)
        thread.join(2.0)
        self.assertIsNone(result["decision"])
        self.assertIsNone(broker.snapshot()["pending"])

    def test_fifo_queue_snapshots_head_and_counts_tail(self) -> None:
        broker = PermissionBroker()
        broker.set_mode(True)
        threads = []
        for index in range(2):
            thread, _ = self._submit_in_thread(
                broker, self._submit(broker, "Bash", tool_input={"command": f"cmd-{index}"}),
                wait_count=index + 1)
            threads.append(thread)
        snapshot = broker.snapshot()
        self.assertEqual(snapshot["pending"]["preview"], "cmd-0")  # 队首 = 最早提交
        self.assertEqual(snapshot["pending"]["queued"], 1)
        self.assertEqual(len(snapshot["queue"]), 2)

        broker.resolve(snapshot["queue"][0]["id"], False)
        threads[0].join(2.0)
        snapshot = broker.snapshot()
        self.assertEqual(snapshot["pending"]["preview"], "cmd-1")  # 自动前进
        self.assertEqual(snapshot["pending"]["queued"], 0)

    def test_overflow_evicts_oldest_with_fail_open(self) -> None:
        broker = PermissionBroker()
        broker.set_mode(True)
        results: list = []

        for index in range(PERMISSION_MAX_PENDING + 1):
            thread, result = self._submit_in_thread(
                broker, self._submit(broker, "Bash", tool_input={"command": f"cmd-{index}"}),
                timeout=5.0, wait_count=min(index + 1, PERMISSION_MAX_PENDING))
            results.append((thread, result))

        snapshot = broker.snapshot()
        self.assertEqual(len(snapshot["queue"]), PERMISSION_MAX_PENDING)
        # 被挤掉的最旧请求空体回落（fail-open），其余仍等待
        results[0][0].join(2.0)
        self.assertIsNone(results[0][1]["decision"])

    def test_disabling_mode_releases_waiters_with_fail_open(self) -> None:
        broker = PermissionBroker()
        broker.set_mode(True)
        thread, result = self._submit_in_thread(
            broker, self._submit(broker, "Write", tool_input={"file_path": "/tmp/a.txt"}))
        self.assertIsNotNone(broker.snapshot()["pending"])
        broker.set_mode(False)
        thread.join(2.0)
        self.assertIsNone(result["decision"])
        self.assertFalse(broker.snapshot()["enabled"])

    def test_preview_picks_salient_field_and_truncates_utf8(self) -> None:
        broker = PermissionBroker()
        broker.set_mode(True)
        long_command = "echo " + "点" * 80
        thread, _ = self._submit_in_thread(
            broker, self._submit(broker, "Bash", tool_input={"command": long_command}))
        pending = broker.snapshot()["pending"]
        encoded = pending["preview"].encode("utf-8")
        self.assertLessEqual(len(encoded), 90)
        self.assertTrue(pending["preview"].startswith("echo"))
        broker.resolve(pending["id"], True)
        thread.join(2.0)

    def test_snapshot_reports_wait_seconds_constant(self) -> None:
        broker = PermissionBroker()
        self.assertEqual(broker.snapshot()["wait_seconds"], PERMISSION_WAIT_SECONDS)

    def test_module_disabled_fails_open_immediately(self) -> None:
        """Claude Code 模块关闭时权限请求不入队、立即空体回落本机提示。"""
        broker = PermissionBroker(module_enabled=lambda: False)
        broker.set_mode(True)
        decision = broker.submit_and_wait(
            self._submit(broker, "Bash", tool_input={"command": "ls"}))
        self.assertIsNone(decision)
        self.assertIsNone(broker.snapshot()["pending"])

        enabled_broker = PermissionBroker(module_enabled=lambda: True)
        enabled_broker.set_mode(True)
        thread, result = self._submit_in_thread(
            enabled_broker, self._submit(enabled_broker, "Bash", tool_input={"command": "ls"}))
        pending = enabled_broker.snapshot()["pending"]
        self.assertIsNotNone(pending)
        enabled_broker.resolve(pending["id"], True)
        thread.join(2.0)
        self.assertEqual(result["decision"], {"behavior": "allow"})


if __name__ == "__main__":
    unittest.main()
