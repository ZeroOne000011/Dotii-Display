from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from claudecode_client import (  # noqa: E402
    HOOK_EVENTS,
    ClaudeCodeMonitor,
    hook_command,
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
            self.assertEqual(sorted(first["changed"]), sorted(HOOK_EVENTS))
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


if __name__ == "__main__":
    unittest.main()
