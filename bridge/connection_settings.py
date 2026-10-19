"""Persistent presentation and transport preferences, shared by all hosts."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

DEFAULTS = {"language": "zh-CN", "device_language": "follow", "mode": "auto", "device_id": "", "port": ""}


class ConnectionSettings:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                return self.validate({**DEFAULTS, **data})
            except (OSError, ValueError, TypeError):
                return dict(DEFAULTS)

    @staticmethod
    def validate(data):
        if not isinstance(data, dict) or set(data) - set(DEFAULTS):
            raise ValueError("Invalid connection settings")
        if data["language"] not in ("zh-CN", "en") or data["device_language"] not in ("follow", "zh-CN", "en"):
            raise ValueError("Invalid language")
        if data["mode"] not in ("auto", "usb", "wifi"):
            raise ValueError("Invalid connection mode")
        for key in ("device_id", "port"):
            if not isinstance(data[key], str) or len(data[key]) > 128:
                raise ValueError("Invalid device selection")
        return {key: data[key] for key in DEFAULTS}

    def write(self, changes):
        if not isinstance(changes, dict):
            raise ValueError("Invalid connection settings")
        with self.lock:
            # A language change always applies to the management center and Dotii.
            # Retain the legacy field for older clients and stored preferences.
            if "language" in changes:
                changes = {**changes, "device_language": "follow"}
            data = self.validate({**self.read(), **changes})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            pending = self.path.with_suffix(".pending")
            pending.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(pending, self.path)
            return data

    def device(self):
        data = self.read()
        return {"language": data["language"] if data["device_language"] == "follow" else data["device_language"], "connection_mode": data["mode"]}
