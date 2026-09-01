from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from codex_app_server import (  # noqa: E402
    AppServerError,
    CodexAppServerSource,
    TRANSIENT_REFRESH_FAILURE_LIMIT,
    account_snapshot,
    probe_app_server,
    thread_snapshot,
)


class CodexAppServerProbeTests(unittest.TestCase):
    def test_probe_reports_account_and_all_read_only_checks(self) -> None:
        responses = {
            "account/read": {"account": {"type": "chatgpt", "email": "user@example.com", "planType": "plus"}},
            "account/rateLimits/read": {"rateLimits": {}},
            "account/usage/read": {"dailyUsageBuckets": []},
            "thread/list": {"data": [{"id": "thread-1"}, {"id": "thread-2"}]},
        }
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            with mock.patch("codex_app_server.AppServerClient") as client_type:
                client_type.return_value.request.side_effect = lambda method, params=None: responses[method]
                result = probe_app_server(Path(temporary), BRIDGE.parent)

        self.assertTrue(result["ok"])
        self.assertTrue(result["cli"]["available"])
        self.assertEqual(result["account"]["email"], "user@example.com")
        self.assertEqual(result["account"]["plan_type"], "plus")
        self.assertEqual(result["thread_count"], 2)
        self.assertEqual(result["checks"], {"rate_limits": True, "usage": True, "threads": True})
        client_type.return_value.start.assert_called_once()
        client_type.return_value.close.assert_called_once()

    def test_probe_reports_logged_out_without_calling_account_data_apis(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            with mock.patch("codex_app_server.AppServerClient") as client_type:
                client_type.return_value.request.return_value = {"account": None, "requiresOpenaiAuth": True}
                result = probe_app_server(Path(temporary), BRIDGE.parent)

        self.assertFalse(result["ok"])
        self.assertTrue(result["app_server"]["available"])
        self.assertFalse(result["account"]["logged_in"])
        self.assertIn("尚未登录", result["detail"])
        self.assertEqual(client_type.return_value.request.call_count, 1)


class CodexAccountSnapshotTests(unittest.TestCase):
    def test_plus_rate_windows_include_five_hour_and_weekly_limits(self) -> None:
        snapshot = account_snapshot({
            "rateLimits": {
                "planType": "plus",
                "primary": {"windowDurationMins": 300, "usedPercent": 32, "resetsAt": 1_800_000_000},
                "secondary": {"windowDurationMins": 10080, "usedPercent": 21, "resetsAt": 1_800_604_800},
            }
        }, {}, now=datetime(2026, 8, 24, tzinfo=timezone.utc))
        self.assertTrue(snapshot["five_hour_available"])
        self.assertEqual(snapshot["five_hour_remaining_percent"], 68)
        self.assertTrue(snapshot["five_hour_reset_date"])
        self.assertTrue(snapshot["weekly_available"])
        self.assertEqual(snapshot["weekly_remaining_percent"], 79)

    def test_codex_weekly_window_wins_over_zero_base_model_placeholder(self) -> None:
        codex_limits = {
            "planType": "plus",
            "primary": {"windowDurationMins": 300, "usedPercent": 32, "resetsAt": 1_800_000_000},
            "secondary": {"windowDurationMins": 10080, "usedPercent": 21, "resetsAt": 1_800_604_800},
        }
        snapshot = account_snapshot({
            "rateLimitsByLimitId": {
                "base_model_inference": {
                    "planType": "plus",
                    "primary": {
                        "windowDurationMins": 10080,
                        "usedPercent": 0,
                        "resetsAt": 1_800_700_000,
                    },
                },
                "codex": codex_limits,
            },
            "rateLimits": codex_limits,
        }, {})
        self.assertEqual(snapshot["five_hour_remaining_percent"], 68)
        self.assertEqual(snapshot["weekly_remaining_percent"], 79)
        self.assertEqual(snapshot["weekly_used_percent"], 21)

    def test_missing_five_hour_window_remains_unavailable(self) -> None:
        snapshot = account_snapshot({
            "rateLimits": {
                "planType": "pro",
                "secondary": {"windowDurationMins": 10080, "usedPercent": 10, "resetsAt": 1_800_604_800},
            }
        }, {})
        self.assertFalse(snapshot["five_hour_available"])
        self.assertEqual(snapshot["five_hour_remaining_percent"], 0)
        self.assertEqual(snapshot["five_hour_reset_date"], "")

    def test_recent_seven_day_tokens_follow_latest_server_bucket(self) -> None:
        snapshot = account_snapshot({}, {
            "dailyUsageBuckets": [
                {"startDate": "2026-08-15", "tokens": 5},
                {"startDate": "2026-08-16", "tokens": 10},
                {"startDate": "2026-08-22", "tokens": 20},
                {"startDate": "invalid", "tokens": 999},
            ],
        }, now=datetime(2026, 8, 24, tzinfo=timezone.utc))
        self.assertTrue(snapshot["weekly_tokens_available"])
        self.assertEqual(snapshot["weekly_tokens"], 30)
        self.assertEqual(snapshot["weekly_tokens_as_of"], "08-22")


class CodexThreadSnapshotTests(unittest.TestCase):
    NOW = 1_800_000_000

    @staticmethod
    def _thread(*, recency: int, updated: int, items: list[dict] | None = None) -> dict:
        return {
            "id": "thread-1",
            "name": "Current task",
            "source": "vscode",
            "recencyAt": recency,
            "updatedAt": updated,
            "status": {"type": "notLoaded"},
            "turns": [{
                "id": "turn-1",
                "status": "interrupted",
                "startedAt": CodexThreadSnapshotTests.NOW - 120,
                "completedAt": CodexThreadSnapshotTests.NOW - 116,
                "items": items or [],
            }],
        }

    def test_recent_empty_detached_turn_is_working_even_with_completed_at(self) -> None:
        snapshot = thread_snapshot(
            self._thread(recency=self.NOW - 30, updated=self.NOW),
            now_epoch=self.NOW,
        )
        self.assertEqual(snapshot["status"], "working")
        self.assertEqual(snapshot["current_action"], "正在处理任务")
        self.assertEqual(snapshot["duration_seconds"], 120)
        self.assertEqual(snapshot["last_updated_epoch"], self.NOW - 30)

    def test_stale_recency_is_not_masked_by_read_repair_updated_at(self) -> None:
        snapshot = thread_snapshot(
            self._thread(recency=self.NOW - 2 * 24 * 3600, updated=self.NOW),
            now_epoch=self.NOW,
        )
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["last_updated_epoch"], self.NOW - 2 * 24 * 3600)

    def test_long_running_detached_turn_stays_working(self) -> None:
        snapshot = thread_snapshot(
            self._thread(recency=self.NOW - 4 * 3600, updated=self.NOW),
            now_epoch=self.NOW,
        )
        self.assertEqual(snapshot["status"], "working")

    def test_interrupted_turn_with_persisted_items_remains_failed(self) -> None:
        snapshot = thread_snapshot(
            self._thread(
                recency=self.NOW - 30,
                updated=self.NOW,
                items=[{"type": "agentMessage", "phase": "final_answer", "text": "Stopped"}],
            ),
            now_epoch=self.NOW,
        )
        self.assertEqual(snapshot["status"], "failed")

    def test_interrupted_turn_with_new_visible_activity_is_working(self) -> None:
        snapshot = thread_snapshot(
            self._thread(
                recency=self.NOW - 5,
                updated=self.NOW,
                items=[
                    {"type": "userMessage", "content": "New request"},
                    {"type": "agentMessage", "phase": "commentary", "text": "Working"},
                    {"type": "commandExecution", "status": "completed"},
                ],
            ),
            now_epoch=self.NOW,
        )
        self.assertEqual(snapshot["status"], "working")
        self.assertEqual(snapshot["current_action"], "Working")

    def test_empty_active_turn_keeps_latest_user_without_replaying_old_agent_output(self) -> None:
        thread = self._thread(recency=self.NOW - 30, updated=self.NOW)
        thread["turns"].insert(0, {
            "id": "turn-completed",
            "status": "completed",
            "startedAt": self.NOW - 600,
            "completedAt": self.NOW - 300,
            "items": [
                {"type": "userMessage", "content": "Earlier question"},
                {"type": "commandExecution", "status": "completed", "command": "private"},
                {"type": "agentMessage", "phase": "final_answer", "text": "Visible answer"},
            ],
        })
        snapshot = thread_snapshot(thread, now_epoch=self.NOW)
        self.assertEqual(snapshot["status"], "working")
        self.assertEqual(snapshot["last_user_message"], "Earlier question")
        self.assertEqual(snapshot["codex_messages"], [])
        self.assertEqual(snapshot["conversation_mode"], "progress")
        self.assertEqual(snapshot["current_action"], "正在处理任务")
        self.assertEqual(snapshot["user_message_count"], 1)
        self.assertEqual(snapshot["message_count"], 1)

    def test_continuation_turn_keeps_latest_user_and_current_progress_separate(self) -> None:
        thread = self._thread(
            recency=self.NOW - 5,
            updated=self.NOW,
            items=[
                {"type": "agentMessage", "phase": "commentary", "text": "Continuing work"},
                {"type": "commandExecution", "status": "inProgress"},
            ],
        )
        thread["turns"][0]["status"] = "inProgress"
        thread["turns"][0]["completedAt"] = None
        thread["turns"].insert(0, {
            "id": "previous-turn",
            "status": "interrupted",
            "startedAt": self.NOW - 600,
            "completedAt": self.NOW - 300,
            "items": [
                {"type": "userMessage", "content": "Original request"},
                {"type": "agentMessage", "phase": "commentary", "text": "Old progress"},
            ],
        })

        snapshot = thread_snapshot(thread, now_epoch=self.NOW)

        self.assertEqual(snapshot["status"], "working")
        self.assertEqual(snapshot["last_user_message"], "Original request")
        self.assertEqual(snapshot["codex_messages"], ["Continuing work"])
        self.assertEqual(snapshot["current_action"], "Continuing work")
        self.assertEqual(snapshot["conversation_mode"], "progress")

    def test_working_turn_keeps_all_current_visible_messages(self) -> None:
        thread = self._thread(
            recency=self.NOW - 5,
            updated=self.NOW,
            items=[
                {"type": "userMessage", "content": "Newest question"},
                {"type": "agentMessage", "phase": "commentary", "text": "First update"},
                {"type": "agentMessage", "phase": "commentary", "text": "Latest update"},
                {"type": "agentMessage", "phase": "final_answer", "text": "Draft answer"},
            ],
        )
        thread["turns"][0]["status"] = "inProgress"
        thread["turns"][0]["completedAt"] = None
        snapshot = thread_snapshot(thread, now_epoch=self.NOW)
        self.assertEqual(snapshot["status"], "working")
        self.assertEqual(snapshot["last_user_message"], "Newest question")
        self.assertEqual(snapshot["codex_messages"], ["First update", "Latest update"])
        self.assertEqual(snapshot["conversation_mode"], "progress")
        self.assertEqual(snapshot["current_action"], "Latest update")

    def test_completed_turn_keeps_only_latest_final_answer(self) -> None:
        thread = self._thread(
            recency=self.NOW - 30,
            updated=self.NOW,
            items=[
                {"type": "userMessage", "content": "Newest question"},
                {"type": "agentMessage", "phase": "commentary", "text": "First update"},
                {"type": "agentMessage", "phase": "commentary", "text": "Latest update"},
                {"type": "agentMessage", "phase": "final_answer", "text": "Final answer"},
            ],
        )
        thread["turns"][0]["status"] = "completed"
        snapshot = thread_snapshot(thread, now_epoch=self.NOW)
        self.assertEqual(snapshot["last_user_message"], "Newest question")
        self.assertEqual(snapshot["codex_messages"], ["Final answer"])
        self.assertEqual(snapshot["last_assistant_message"], "Final answer")
        self.assertEqual(snapshot["conversation_mode"], "final")
        self.assertEqual(snapshot["current_action"], "任务已完成")

    def test_user_message_count_covers_the_whole_thread(self) -> None:
        thread = self._thread(
            recency=self.NOW - 30,
            updated=self.NOW,
            items=[
                {"type": "userMessage", "content": "Newest question"},
                {"type": "agentMessage", "phase": "final_answer", "text": "Newest answer"},
            ],
        )
        thread["turns"][0]["status"] = "completed"
        thread["turns"].append({
            "id": "older-turn",
            "status": "completed",
            "startedAt": self.NOW - 600,
            "completedAt": self.NOW - 500,
            "items": [
                {"type": "userMessage", "content": "Older question"},
                {"type": "agentMessage", "phase": "final_answer", "text": "Older answer"},
            ],
        })
        snapshot = thread_snapshot(thread, now_epoch=self.NOW)
        self.assertEqual(snapshot["user_message_count"], 2)
        self.assertEqual(snapshot["message_count"], 2)

    def test_turns_are_selected_by_timestamp_not_response_order(self) -> None:
        newer = {
            "id": "newer",
            "status": "completed",
            "startedAt": self.NOW - 100,
            "completedAt": self.NOW - 50,
            "items": [
                {"type": "userMessage", "content": "Newest question"},
                {"type": "agentMessage", "phase": "final_answer", "text": "Newest answer"},
            ],
        }
        older = {
            "id": "older",
            "status": "completed",
            "startedAt": self.NOW - 500,
            "completedAt": self.NOW - 400,
            "items": [
                {"type": "userMessage", "content": "Old question"},
                {"type": "agentMessage", "phase": "final_answer", "text": "Old answer"},
            ],
        }
        thread = self._thread(recency=self.NOW - 30, updated=self.NOW)
        thread["turns"] = [newer, older]
        snapshot = thread_snapshot(thread, now_epoch=self.NOW)
        self.assertEqual(snapshot["turn_id"], "newer")
        self.assertEqual(snapshot["last_user_message"], "Newest question")
        self.assertEqual(snapshot["codex_messages"], ["Newest answer"])

    def test_conversation_messages_are_not_truncated(self) -> None:
        items = [{"type": "userMessage", "content": "Question"}]
        items.extend(
            {"type": "agentMessage", "phase": "commentary", "text": f"update-{index}-" + "x" * 900}
            for index in range(6)
        )
        thread = self._thread(recency=self.NOW - 5, updated=self.NOW, items=items)
        thread["turns"][0]["status"] = "inProgress"
        thread["turns"][0]["completedAt"] = None
        snapshot = thread_snapshot(thread, now_epoch=self.NOW)
        self.assertEqual(len(snapshot["codex_messages"]), 6)
        self.assertTrue(snapshot["codex_messages"][0].startswith("update-0-"))
        self.assertEqual(len(snapshot["codex_messages"][-1]), len("update-5-") + 900)


class CodexMultiTaskTests(unittest.TestCase):
    NOW = 1_800_000_000

    def test_refresh_keeps_multiple_active_tasks_and_uses_recency_sort(self) -> None:
        def active_thread(thread_id: str, started: int) -> dict:
            return {
                "id": thread_id,
                "name": thread_id,
                "source": "vscode",
                "recencyAt": self.NOW - 5,
                "status": {"type": "active"},
                "turns": [{
                    "id": f"turn-{thread_id}",
                    "status": "inProgress",
                    "startedAt": started,
                    "completedAt": None,
                    "items": [
                        {"type": "userMessage", "content": f"question-{thread_id}"},
                        {"type": "agentMessage", "phase": "commentary", "text": f"update-{thread_id}"},
                    ],
                }],
            }

        details = {
            "thread-a": active_thread("thread-a", self.NOW - 100),
            "thread-b": active_thread("thread-b", self.NOW - 200),
        }

        class FakeClient:
            def __init__(self) -> None:
                self.list_params = None

            def request(self, method, params=None):
                if method == "thread/list":
                    self.list_params = params
                    return {"data": [{"id": key, **value} for key, value in details.items()]}
                if method == "thread/read":
                    return {"thread": details[params["threadId"]]}
                raise AssertionError(method)

        source = CodexAppServerSource.__new__(CodexAppServerSource)
        source.client = FakeClient()
        source.task = thread_snapshot(None)
        source.tasks = [source.task]
        source.active_thread_ids = set()
        source.refresh_thread()

        self.assertEqual(source.client.list_params["sortKey"], "recency_at")
        self.assertEqual([task["thread_id"] for task in source.tasks], ["thread-b", "thread-a"])
        self.assertEqual([task["conversation_mode"] for task in source.tasks], ["progress", "progress"])

    def test_refresh_skips_a_thread_that_cannot_be_deserialized(self) -> None:
        readable = {
            "id": "thread-readable",
            "name": "readable",
            "source": "vscode",
            "recencyAt": self.NOW - 5,
            "turns": [{
                "id": "turn-readable",
                "status": "completed",
                "startedAt": self.NOW - 20,
                "completedAt": self.NOW - 5,
                "items": [{"type": "userMessage", "content": "question"}],
            }],
        }

        class FakeClient:
            def request(self, method, params=None):
                if method == "thread/list":
                    return {"data": [
                        {"id": "thread-unreadable", "name": "new item"},
                        readable,
                    ]}
                if method == "thread/read" and params["threadId"] == "thread-unreadable":
                    raise AppServerError("unknown variant functionCallOutput")
                if method == "thread/read":
                    return {"thread": readable}
                raise AssertionError(method)

        source = CodexAppServerSource.__new__(CodexAppServerSource)
        source.client = FakeClient()
        source.task = thread_snapshot(None)
        source.tasks = [source.task]
        source.active_thread_ids = set()
        source.refresh_thread()

        self.assertEqual([task["thread_id"] for task in source.tasks], ["thread-readable"])


class CodexSnapshotResilienceTests(unittest.TestCase):
    @staticmethod
    def _source() -> CodexAppServerSource:
        source = CodexAppServerSource.__new__(CodexAppServerSource)
        source.account = {"plan_type": "plus"}
        source.task = thread_snapshot(None)
        source.tasks = [source.task]
        source._account_refresh_failures = 0
        source._thread_refresh_failures = 0
        source._has_account_snapshot = True
        source._has_thread_snapshot = True
        source.refresh_thread = lambda: None
        return source

    def test_transient_account_failure_keeps_last_successful_snapshot(self) -> None:
        source = self._source()

        def fail_account() -> None:
            raise AppServerError("temporary")

        source.refresh_account = fail_account
        snapshot = source.snapshot()
        self.assertEqual(snapshot["codex"]["plan_type"], "plus")
        self.assertEqual(source._account_refresh_failures, 1)

    def test_repeated_account_failure_eventually_restarts_source(self) -> None:
        source = self._source()

        def fail_account() -> None:
            raise AppServerError("temporary")

        source.refresh_account = fail_account
        for _ in range(TRANSIENT_REFRESH_FAILURE_LIMIT - 1):
            source.snapshot()
        with self.assertRaises(AppServerError):
            source.snapshot()


if __name__ == "__main__":
    unittest.main()
