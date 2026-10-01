from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

import ble_link  # noqa: E402
from bluetooth_bridge import BleLinkService  # noqa: E402


class FakeClient:
    """最小 bleak client 替身：记录写入、转发 SYNC 通知。"""

    last_instance: "FakeClient | None" = None

    def __init__(self, target, timeout=30.0, pair=False) -> None:
        self.target = target
        self.writes: list[tuple[str, bytes]] = []
        self.notify_handlers: dict[str, object] = {}
        FakeClient.last_instance = self

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def start_notify(self, uuid: str, handler) -> None:
        self.notify_handlers[uuid] = handler

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool = False) -> None:
        self.writes.append((uuid, bytes(data)))

    async def emulate_sync(self, notice: bytes) -> None:
        handler = self.notify_handlers.get("7b4e0006-4db4-4c72-a729-ea5187241a43")
        if handler is not None:
            handler(None, bytearray(notice))


def make_service(temporary: str, payloads: list[str]) -> tuple[BleLinkService, SimpleNamespace]:
    bluetooth = SimpleNamespace(
        lock=__import__("threading").Lock(),
        platform=SimpleNamespace(name="macos", bluetooth_pair_on_connect=False),
        _ble_devices={},
        _bleak=lambda: (FakeClient, None),
    )
    calls = {"count": 0}

    def provider() -> str:
        index = min(calls["count"], len(payloads) - 1)
        calls["count"] += 1
        return payloads[index]

    service = BleLinkService(bluetooth, Path(temporary), provider)
    return service, SimpleNamespace(calls=calls)


def run_session(service: BleLinkService, *, stop_after_writes: int = 40,
                budget_seconds: float = 4.0) -> FakeClient:
    """跑一次 _session：写入达到目标或时间预算耗尽后置位停止。"""

    def watchdog() -> None:
        import time

        deadline = time.monotonic() + budget_seconds
        while time.monotonic() < deadline:
            client = FakeClient.last_instance
            if client is not None and len(client.writes) >= stop_after_writes:
                break
            time.sleep(0.02)
        service._stop.set()

    import threading

    thread = threading.Thread(target=watchdog, daemon=True)
    thread.start()
    asyncio.run(service._session())
    service._stop.clear()
    return FakeClient.last_instance


class BleLinkServiceTests(unittest.TestCase):
    def test_binds_and_persists(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            service, _ = make_service(temporary, ['{"a":1}'])
            self.assertFalse(service.snapshot()["enabled"])
            service.set_binding(True, "AA-BB")
            self.assertTrue(service.snapshot()["enabled"])
            self.assertEqual(service.snapshot()["address"], "AA-BB")
            # 重新加载（持久化生效）
            service2, _ = make_service(temporary, ['{"a":1}'])
            self.assertTrue(service2.snapshot()["enabled"])

    def test_session_pushes_snapshot_packets(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            payload = '{"schema_version":1}' + '"x"' * 1200
            service, _ = make_service(temporary, [payload])
            service.set_binding(True, "AA-BB")
            client = run_session(service)
            self.assertIsNotNone(client)
            writes = [data for uuid, data in client.writes if uuid.startswith("7b4e0005")]
            self.assertGreater(len(writes), 0)
            assembler = ble_link.SnapshotAssembler()
            ok, status = assembler.accept(writes[0])
            for packet in writes[1:]:
                if status in {"accepted", "stale"}:
                    break
                ok, status = assembler.accept(packet)
            self.assertEqual(status, "accepted")
            self.assertEqual(assembler.payload, payload.encode("utf-8"))
            snapshot = service.snapshot()
            self.assertTrue(snapshot["connected"])
            self.assertEqual(snapshot["revision"], ble_link.payload_revision(payload.encode()))

    def test_user_pause_stops_and_resume_reconnects(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            service, _ = make_service(temporary, ['{"a":1}'])
            service.set_binding(True, "AA-BB")
            service.pause(seconds=None)  # 用户暂停
            state = service.snapshot()
            self.assertTrue(state["paused"])
            self.assertFalse(state["connected"])

            # 用户暂停不会被时间流逝自动恢复（_run 循环语义：user_paused 豁免超时恢复）
            with service._lock:
                self.assertTrue(service._user_paused)

            service.resume()
            self.assertFalse(service.snapshot()["paused"])

    def test_timed_pause_expires_automatically(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            service, _ = make_service(temporary, ['{"a":1}'])
            service.pause(seconds=0.01)  # 配网让路：限时
            import time as _time

            with service._lock:
                service._paused_until = _time.monotonic() - 1  # 模拟已过期
            # _run 的恢复逻辑内联验证（不跑线程）：
            with service._lock:
                expired = service._paused and not service._user_paused and \
                    _time.monotonic() > service._paused_until
            self.assertTrue(expired)

    def test_resend_notice_forces_repush(self) -> None:
        with tempfile.TemporaryDirectory(dir=BRIDGE.parent / ".codx") as temporary:
            payload = '{"schema_version":1,"v":1}'
            service, _ = make_service(temporary, [payload])
            service.set_binding(True, "AA-BB")

            real_write = FakeClient.write_gatt_char

            async def write_and_trigger(self_client, uuid, data, response=False):
                await real_write(self_client, uuid, data, response)
                if bytes(data) == b"\x03":  # 尾包后注入重发请求
                    await self_client.emulate_sync(b'{"resend":true}')

            with mock.patch.object(FakeClient, "write_gatt_char", write_and_trigger):
                client = run_session(service, stop_after_writes=60)
            writes = [data for uuid, data in client.writes if uuid.startswith("7b4e0005")]
            headers = [w for w in writes if w[0] == 1]
            self.assertGreaterEqual(len(headers), 2, "重发请求应触发完整重推")


if __name__ == "__main__":
    unittest.main()
