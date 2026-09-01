from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from codex_bridge import (  # noqa: E402
    CODEX_CLI_PACKAGE,
    default_dotii_config,
    dotii_state,
    packaged_startup_command,
    startup_command,
    validate_display_config,
    validate_dotii_config,
    validate_module_config,
    validate_snapshot,
)


class CodexUiConfigTests(unittest.TestCase):
    def test_codex_cli_package_is_pinned(self) -> None:
        self.assertEqual(CODEX_CLI_PACKAGE, "@openai/codex@0.151.0")

    def test_module_config_migrates_dotii_as_enabled(self) -> None:
        modules = validate_module_config({"codex": True, "bambu": False})
        self.assertEqual(modules, {"codex": True, "bambu": False, "dotii": True})

    def test_dotii_state_prioritizes_failure_then_active_work(self) -> None:
        failed = dotii_state(
            {"codex": {"task": {"status": "failed"}}},
            {"configured": True, "connected": True, "status": "printing"},
            True,
        )
        working = dotii_state(
            {"codex": {"task": {"status": "idle"}}},
            {"configured": True, "connected": True, "status": "printing"},
            True,
        )
        self.assertEqual(failed["expression"], "failure")
        self.assertEqual(working["state"], "bambu_printing")
        self.assertEqual(working["expression"], "working")

    def test_dotii_state_maps_waiting_and_offline(self) -> None:
        waiting = dotii_state(
            {"codex": {"task": {"status": "waiting_user"}}}, {}, True,
        )
        offline = dotii_state(
            {"codex": {"task": {"status": "offline"}}}, {}, True,
        )
        self.assertEqual(waiting["expression"], "curious")
        self.assertEqual(offline["expression"], "connecting")

    def test_dotii_state_uses_complete_for_completed_print(self) -> None:
        state = dotii_state(
            {"codex": {"task": {"status": "idle"}}},
            {"configured": True, "connected": True, "status": "completed"},
            True,
        )
        self.assertEqual(state["expression"], "complete")

    def test_dotii_config_allows_one_animation_to_cover_multiple_states(self) -> None:
        config = default_dotii_config()
        config["animations"]["curious"]["states"].remove("codex_waiting_user")
        config["animations"]["working"]["states"].append("codex_waiting_user")
        config["animations"]["working"]["state_duration_ms"] = 3000
        validated = validate_dotii_config(config)
        state = dotii_state(
            {"codex": {"task": {"status": "waiting_user"}}}, {}, True, validated,
        )
        self.assertEqual(state["expression"], "working")
        self.assertEqual(state["state_duration_ms"], 3000)
        self.assertFalse(state["state_hold"])
        self.assertEqual(
            validated["animations"]["working"]["states"],
            ["codex_working", "bambu_printing", "codex_waiting_user"],
        )

    def test_dotii_config_rejects_duplicate_business_states(self) -> None:
        duplicate = default_dotii_config()
        duplicate["animations"]["complete"]["states"].append("codex_working")
        with self.assertRaises(ValueError):
            validate_dotii_config(duplicate)

    def test_dotii_config_unassigned_business_states_keep_plain_status_reason(self) -> None:
        config = default_dotii_config()
        config["animations"]["working"]["states"].remove("codex_working")
        config["animations"]["working"]["states"].remove("bambu_printing")

        validated = validate_dotii_config(config)
        codex = dotii_state(
            {"codex": {"task": {"status": "working"}}}, {}, True, validated,
        )
        bambu = dotii_state(
            {"codex": {"task": {"status": "idle"}}},
            {"configured": True, "connected": True, "status": "printing"},
            True,
            validated,
        )

        self.assertEqual(codex["expression"], "idle_breath")
        self.assertFalse(codex["state_assigned"])
        self.assertEqual(codex["state_duration_ms"], 0)
        self.assertFalse(codex["state_hold"])
        self.assertEqual(codex["reason"], "Codex 正在工作")
        self.assertEqual(bambu["expression"], "idle_breath")
        self.assertFalse(bambu["state_assigned"])
        self.assertEqual(bambu["reason"], "Bambu 正在打印")

    def test_dotii_config_validates_animation_duration(self) -> None:
        config = default_dotii_config()
        config["animations"]["working"]["state_duration_ms"] = 2400
        with self.assertRaises(ValueError):
            validate_dotii_config(config)

        for duration in (0, 1000, 3000, 5000, 30000):
            config["animations"]["working"]["state_duration_ms"] = duration
            self.assertEqual(
                validate_dotii_config(config)["animations"]["working"]["state_duration_ms"],
                duration,
            )

    def test_dotii_config_migrates_shared_legacy_module_states(self) -> None:
        config = default_dotii_config()
        replacements = {
            "codex_waiting_user": "waiting_user",
            "codex_working": "working",
            "codex_completed": "completed",
            "bambu_completed": "print_completed",
            "codex_failure": "failure",
        }
        for animation in config["animations"].values():
            animation["states"] = [replacements.get(item, item) for item in animation["states"]]
        config["animations"]["curious"]["states"].remove("bambu_paused")
        config["animations"]["working"]["states"].remove("bambu_printing")
        config["animations"]["failure"]["states"].remove("bambu_failure")

        migrated = validate_dotii_config(config)

        self.assertIn("codex_working", migrated["animations"]["working"]["states"])
        self.assertIn("bambu_printing", migrated["animations"]["working"]["states"])
        self.assertIn("codex_failure", migrated["animations"]["failure"]["states"])
        self.assertIn("bambu_failure", migrated["animations"]["failure"]["states"])

    def test_dotii_config_migrates_removed_happy_animation_to_complete(self) -> None:
        config = default_dotii_config()
        config["animations"]["complete"]["states"].remove("bambu_completed")
        config["animations"]["happy"] = {
            "states": ["bambu_completed"],
            "state_duration_ms": 3000,
        }

        migrated = validate_dotii_config(config)

        self.assertNotIn("happy", migrated["animations"])
        self.assertIn("bambu_completed", migrated["animations"]["complete"]["states"])

    def test_dotii_config_migrates_old_editable_fixed_states_to_read_only_defaults(self) -> None:
        config = default_dotii_config()
        for animation in config["animations"].values():
            animation["duration_ms"] = 2400
            animation.pop("state_duration_ms", None)
        config["animations"]["idle_breath"]["states"].append("idle")
        config["animations"]["blink"]["states"].append("blink")
        config["animations"]["touch_response"]["states"].extend(["touch", "codex_working"])
        config["animations"]["working"]["states"].remove("codex_working")

        migrated = validate_dotii_config(config)

        self.assertEqual(migrated["animations"]["idle_breath"]["states"], [])
        self.assertEqual(migrated["animations"]["touch_response"]["states"], [])
        self.assertIn("codex_working", migrated["animations"]["working"]["states"])
        self.assertEqual(migrated["animations"]["working"]["state_duration_ms"], 0)
        self.assertEqual(len(migrated["fixed_states"]), 5)
        self.assertEqual([group["id"] for group in migrated["state_groups"]], ["codex", "bambu"])

    def test_dotii_state_distinguishes_codex_working_from_bambu_printing(self) -> None:
        config = default_dotii_config()
        config["animations"]["working"]["states"].remove("bambu_printing")
        config["animations"]["complete"]["states"].append("bambu_printing")

        codex = dotii_state(
            {"codex": {"task": {"status": "working"}}}, {}, True, config,
        )
        bambu = dotii_state(
            {"codex": {"task": {"status": "idle"}}},
            {"configured": True, "connected": True, "status": "printing"},
            True,
            config,
        )

        self.assertEqual(codex["state"], "codex_working")
        self.assertEqual(codex["expression"], "working")
        self.assertEqual(bambu["state"], "bambu_printing")
        self.assertEqual(bambu["expression"], "complete")

    def test_dotii_state_keeps_fixed_local_mapping_and_loop_durations(self) -> None:
        state = dotii_state({"codex": {"task": {"status": "idle"}}}, {}, True)
        self.assertEqual(state["local_states"]["touch"]["expression"], "touch_response")
        self.assertEqual(state["local_states"]["touch"]["duration_ms"], 1200)
        self.assertEqual(state["local_states"]["blink"]["expression"], "blink")
        self.assertEqual(state["local_states"]["long_idle"]["expression"], "sleepy_yawn")
        self.assertEqual(len(state["durations_ms"]), 9)

    def test_dotii_state_token_changes_only_with_logical_state(self) -> None:
        working = dotii_state(
            {"codex": {"task": {"status": "working"}}}, {}, True,
        )
        working_again = dotii_state(
            {"codex": {"task": {"status": "working"}}}, {}, True,
        )
        completed = dotii_state(
            {"codex": {"task": {"status": "completed"}}}, {}, True,
        )
        self.assertEqual(working["state_token"], working_again["state_token"])
        self.assertNotEqual(working["state_token"], completed["state_token"])

    def test_display_config_defaults_to_classic_and_accepts_dual_limit(self) -> None:
        self.assertEqual(validate_display_config(None)["codex_ui"], "classic")
        self.assertEqual(validate_display_config({"codex_ui": "dual_limit"})["codex_ui"], "dual_limit")

    def test_display_config_rejects_unknown_codex_ui(self) -> None:
        with self.assertRaises(ValueError):
            validate_display_config({"codex_ui": "replace-classic"})

    def test_display_config_accepts_screen_off_page_targets(self) -> None:
        for target in ("none", "custom", "dotii"):
            self.assertEqual(
                validate_display_config({"screen_off_page": target})["screen_off_page"],
                target,
            )

    def test_display_config_rejects_unknown_screen_off_page_target(self) -> None:
        with self.assertRaises(ValueError):
            validate_display_config({"screen_off_page": "bambu"})

    def test_startup_command_uses_absolute_paths_and_startup_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            pythonw = folder / "pythonw.exe"
            app_script = folder / "Dotii 管理中心" / "bridge_app.py"
            pythonw.touch()
            app_script.parent.mkdir()
            app_script.touch()

            self.assertEqual(
                startup_command(pythonw, app_script),
                f'"{pythonw.resolve()}" -B "{app_script.resolve()}" --startup',
            )

    def test_packaged_startup_targets_management_center_not_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            management_center = Path(temporary) / "DotiiManagementCenter.exe"
            management_center.touch()
            command = packaged_startup_command(management_center)
            self.assertEqual(command, f'"{management_center.resolve()}" --startup')
            self.assertNotIn("DotiiBridge.exe", command)

    def test_display_config_migrates_charging_timeouts_from_existing_pair(self) -> None:
        display = validate_display_config({
            "screen_off_timeout_seconds": 30,
            "sleep_timeout_seconds": 180,
        })
        self.assertEqual(display["charging_screen_off_timeout_seconds"], 30)
        self.assertEqual(display["charging_sleep_timeout_seconds"], 180)

    def test_display_config_keeps_independent_power_timeout_pairs(self) -> None:
        display = validate_display_config({
            "screen_off_timeout_seconds": 30,
            "sleep_timeout_seconds": 180,
            "charging_screen_off_timeout_seconds": 300,
            "charging_sleep_timeout_seconds": 0,
        })
        self.assertEqual(display["screen_off_timeout_seconds"], 30)
        self.assertEqual(display["sleep_timeout_seconds"], 180)
        self.assertEqual(display["charging_screen_off_timeout_seconds"], 300)
        self.assertEqual(display["charging_sleep_timeout_seconds"], 0)

    def test_display_config_rejects_invalid_charging_timeout_order(self) -> None:
        with self.assertRaises(ValueError):
            validate_display_config({
                "charging_screen_off_timeout_seconds": 300,
                "charging_sleep_timeout_seconds": 60,
            })

    def test_snapshot_keeps_optional_five_hour_limit(self) -> None:
        snapshot = validate_snapshot({
            "schema_version": 1,
            "codex": {
                "five_hour_available": True,
                "five_hour_remaining_percent": 68,
                "five_hour_reset_date": "08-26 22:40",
                "weekly_remaining_percent": 79,
                "weekly_tokens": 0,
                "task": {"status": "idle"},
            },
        })
        self.assertTrue(snapshot["codex"]["five_hour_available"])
        self.assertEqual(snapshot["codex"]["five_hour_remaining_percent"], 68)
        self.assertEqual(snapshot["codex"]["five_hour_reset_date"], "08-26 22:40")

    def test_snapshot_keeps_complete_codex_detail_text(self) -> None:
        long_text = "完整回复" * 2000
        snapshot = validate_snapshot({
            "schema_version": 1,
            "codex": {
                "weekly_remaining_percent": 79,
                "weekly_tokens": 0,
                "task": {
                    "status": "completed",
                    "last_user_message": long_text,
                    "codex_messages": [long_text],
                },
            },
        })
        task = snapshot["codex"]["task"]
        self.assertEqual(task["last_user_message"], long_text)
        self.assertEqual(task["codex_messages"], [long_text])


if __name__ == "__main__":
    unittest.main()
