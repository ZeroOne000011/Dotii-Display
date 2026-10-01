from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from zai_client import (  # noqa: E402
    ZaiConfigStore,
    ZaiService,
    usage_snapshot,
)


def quota_payload(percentage=32, reset="2026-10-01 14:40:00", level="PRO", **extra):
    limit = {"type": "TOKENS_LIMIT", "percentage": percentage}
    if reset is not None:
        limit["nextResetTime"] = reset
    limits = [limit, {"type": "TIME_LIMIT", "percentage": 5}]
    return {"success": True, "data": {"level": level, "limits": limits}, **extra}


class UsageSnapshotTests(unittest.TestCase):
    def test_tokens_limit_maps_to_remaining_percent_and_reset_date(self) -> None:
        snapshot = usage_snapshot(quota_payload(percentage=32), datetime(2026, 10, 1, 12, 0))
        self.assertEqual(snapshot["plan_level"], "PRO")
        self.assertTrue(snapshot["five_hour_available"])
        self.assertEqual(snapshot["five_hour_remaining_percent"], 68)
        self.assertEqual(snapshot["five_hour_reset_date"], "10-01 14:40")
        self.assertEqual(snapshot["error"], "")

    def test_iso_reset_time_without_timezone_is_accepted(self) -> None:
        snapshot = usage_snapshot(quota_payload(reset="2026-10-01T14:40:00"))
        self.assertEqual(snapshot["five_hour_reset_date"], "10-01 14:40")

    def test_zulu_reset_time_is_converted_to_local_time(self) -> None:
        snapshot = usage_snapshot(quota_payload(reset="2026-10-01T06:40:00Z"), datetime(2026, 10, 1, 12, 0))
        expected = datetime.fromisoformat("2026-10-01T06:40:00+00:00").astimezone().strftime("%m-%d %H:%M")
        self.assertEqual(snapshot["five_hour_reset_date"], expected)

    def test_invalid_reset_time_degrades_to_empty(self) -> None:
        snapshot = usage_snapshot(quota_payload(reset="not-a-time"))
        self.assertTrue(snapshot["five_hour_available"])
        self.assertEqual(snapshot["five_hour_reset_date"], "")

    def test_percentage_is_clamped_between_zero_and_hundred(self) -> None:
        self.assertEqual(usage_snapshot(quota_payload(percentage=-12))["five_hour_remaining_percent"], 100)
        self.assertEqual(usage_snapshot(quota_payload(percentage=145))["five_hour_remaining_percent"], 0)
        self.assertEqual(usage_snapshot(quota_payload(percentage=100))["five_hour_remaining_percent"], 0)
        self.assertEqual(usage_snapshot(quota_payload(percentage=0))["five_hour_remaining_percent"], 100)

    def test_missing_tokens_limit_reports_unavailable(self) -> None:
        payload = {"success": True, "data": {"level": "LITE", "limits": [{"type": "TIME_LIMIT", "percentage": 5}]}}
        snapshot = usage_snapshot(payload)
        self.assertFalse(snapshot["five_hour_available"])
        self.assertEqual(snapshot["error"], "接口未提供 5 小时窗口数据")

    def test_unknown_limit_types_are_ignored(self) -> None:
        payload = quota_payload()
        payload["data"]["limits"].insert(0, {"type": "FUTURE_LIMIT", "percentage": 90})
        snapshot = usage_snapshot(payload)
        self.assertEqual(snapshot["five_hour_remaining_percent"], 68)

    def test_failed_response_reports_message(self) -> None:
        snapshot = usage_snapshot({"success": False, "msg": "令牌无效"})
        self.assertFalse(snapshot["five_hour_available"])
        self.assertEqual(snapshot["error"], "令牌无效")

    def test_invalid_payload_shapes_fall_back_to_error(self) -> None:
        for payload in (None, [], "text", {"success": True}, {"success": True, "data": "x"}):
            snapshot = usage_snapshot(payload)
            self.assertFalse(snapshot["five_hour_available"])
            self.assertTrue(snapshot["error"])

    def test_long_level_is_truncated(self) -> None:
        snapshot = usage_snapshot(quota_payload(level="SUPER-ULTRA-PLAN-NAME"))
        self.assertLessEqual(len(snapshot["plan_level"]), 15)

    def test_credit_limits_with_epoch_milliseconds_pick_earliest_window(self) -> None:
        """真实接口形态：CREDIT_LIMIT + unit/number + 毫秒时间戳。"""
        payload = {
            "code": 200, "msg": "Operation successful", "success": True,
            "data": {
                "level": "pro",
                "limits": [
                    {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "usage": 12000,
                     "currentValue": 4620, "remaining": 7379, "percentage": 38,
                     "nextResetTime": 1790828271625},
                    {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "usage": 60000,
                     "currentValue": 48307, "remaining": 11692, "percentage": 80,
                     "nextResetTime": 1790941648947},
                ],
            },
        }
        snapshot = usage_snapshot(payload, datetime.fromtimestamp(1790828271.625))
        self.assertEqual(snapshot["plan_level"], "PRO")
        self.assertTrue(snapshot["five_hour_available"])
        self.assertEqual(snapshot["five_hour_remaining_percent"], 62)
        expected = datetime.fromtimestamp(1790828271.625).strftime("%m-%d %H:%M")
        self.assertEqual(snapshot["five_hour_reset_date"], expected)

    def test_weekly_window_is_exposed_alongside_five_hour(self) -> None:
        payload = {
            "success": True,
            "data": {"level": "pro", "limits": [
                {"type": "CREDIT_LIMIT", "percentage": 38, "nextResetTime": 1790828271625},
                {"type": "CREDIT_LIMIT", "percentage": 80, "nextResetTime": 1790941648947},
            ]},
        }
        snapshot = usage_snapshot(payload)
        self.assertTrue(snapshot["weekly_available"])
        self.assertEqual(snapshot["weekly_remaining_percent"], 20)
        expected = datetime.fromtimestamp(1790941648.947).strftime("%m-%d %H:%M")
        self.assertEqual(snapshot["weekly_reset_date"], expected)

    def test_single_window_keeps_weekly_unavailable(self) -> None:
        payload = quota_payload(percentage=50)
        snapshot = usage_snapshot(payload)
        self.assertTrue(snapshot["five_hour_available"])
        self.assertFalse(snapshot["weekly_available"])
        self.assertEqual(snapshot["weekly_remaining_percent"], 0)
        self.assertEqual(snapshot["weekly_reset_date"], "")

    def test_credit_limit_epoch_seconds_and_numeric_string_are_accepted(self) -> None:
        base = {"type": "CREDIT_LIMIT", "percentage": 10}
        for reset in (1790828271, "1790828271625"):
            payload = {"success": True, "data": {"level": "pro", "limits": [{**base, "nextResetTime": reset}]}}
            snapshot = usage_snapshot(payload)
            self.assertTrue(snapshot["five_hour_available"], msg=str(reset))
            self.assertEqual(snapshot["five_hour_remaining_percent"], 90)
            self.assertTrue(snapshot["five_hour_reset_date"])

    def test_invalid_epoch_falls_back_to_empty_reset_date(self) -> None:
        payload = {"success": True, "data": {"level": "pro",
                   "limits": [{"type": "CREDIT_LIMIT", "percentage": 38, "nextResetTime": -1}]}}
        snapshot = usage_snapshot(payload)
        self.assertTrue(snapshot["five_hour_available"])
        self.assertEqual(snapshot["five_hour_reset_date"], "")


class ZaiConfigStoreTests(unittest.TestCase):
    def test_public_view_never_exposes_the_api_key(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            store = ZaiConfigStore(Path(temporary) / "zai.json")
            store.write({"api_key": "secret-key", "platform": "cn"})
            public = store.public()
            self.assertNotIn("api_key", public)
            self.assertNotIn("secret-key", str(public))
            self.assertTrue(public["has_api_key"])
            self.assertTrue(public["configured"])
            self.assertEqual(public["platform"], "cn")

    def test_preserve_secret_keeps_previous_key_when_omitted(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            store = ZaiConfigStore(Path(temporary) / "zai.json")
            store.write({"api_key": "first-key", "platform": "cn"})
            store.write({"api_key": "", "platform": "intl"}, preserve_secret=True)
            self.assertEqual(store.read()["api_key"], "first-key")
            self.assertEqual(store.read()["platform"], "intl")

    def test_unknown_platform_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            store = ZaiConfigStore(Path(temporary) / "zai.json")
            with self.assertRaises(ValueError):
                store.write({"api_key": "key", "platform": "us"})

    def test_api_key_with_whitespace_or_too_long_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            store = ZaiConfigStore(Path(temporary) / "zai.json")
            with self.assertRaises(ValueError):
                store.write({"api_key": "abc def", "platform": "cn"})
            with self.assertRaises(ValueError):
                store.write({"api_key": "x" * 201, "platform": "cn"})

    def test_corrupt_file_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            path = Path(temporary) / "zai.json"
            path.write_text("{broken", encoding="utf-8")
            store = ZaiConfigStore.__new__(ZaiConfigStore)
            store.path = path
            store._lock = __import__("threading").Lock()
            self.assertEqual(store.read(), {"api_key": "", "platform": "cn"})


class ZaiServiceStateTests(unittest.TestCase):
    @staticmethod
    def _service(temporary: str, enabled: bool) -> ZaiService:
        config = ZaiConfigStore(Path(temporary) / "zai.json")
        return ZaiService(config, lambda: enabled)

    def test_disabled_module_reports_disabled_state(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            service = self._service(temporary, enabled=False)
            service.config_store.write({"api_key": "key", "platform": "cn"})
            snapshot = service.snapshot()
            self.assertEqual(snapshot["service_state"], "disabled")
            self.assertFalse(snapshot["connected"])

    def test_enabled_without_key_reports_needs_configuration(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            service = self._service(temporary, enabled=True)
            snapshot = service.snapshot()
            self.assertEqual(snapshot["service_state"], "needs_configuration")
            self.assertFalse(snapshot["configured"])

    def test_enabled_and_configured_starts_connecting_without_requests(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            service = self._service(temporary, enabled=True)
            service.config_store.write({"api_key": "key", "platform": "cn"})
            snapshot = service.snapshot()
            self.assertEqual(snapshot["service_state"], "connecting")
            self.assertFalse(snapshot["connected"])

    def test_fetch_without_key_raises_friendly_error(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            service = self._service(temporary, enabled=True)
            with self.assertRaises(Exception) as context:
                service._fetch()
            self.assertIn("API Key", str(context.exception))

    def test_business_failure_is_error_not_online(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            service = self._service(temporary, enabled=True)
            service.config_store.write({"api_key": "key", "platform": "cn"})
            failure = usage_snapshot({"success": False, "msg": "token expired or incorrect"})
            service._usage = failure
            service._last_error = failure["error"]
            snapshot = service.snapshot()
            self.assertFalse(snapshot["connected"])
            self.assertEqual(snapshot["service_state"], "error")
            self.assertEqual(snapshot["last_error"], "token expired or incorrect")


if __name__ == "__main__":
    unittest.main()
