"""Translate administrative system labels without touching device/user content."""
from __future__ import annotations

import json
import re

from runtime_paths import resource_path


def catalog():
    try:
        return json.loads(resource_path("web", "translations.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


CATALOG = catalog()


def system_text(value):
    if not isinstance(value, str):
        return value
    if value in CATALOG:
        return CATALOG[value]
    for source, target in CATALOG.items():
        if "{0}" not in source:
            continue
        pattern = re.escape(source)
        for index in range(5):
            pattern = pattern.replace(re.escape("{" + str(index) + "}"), "(.+?)")
        match = re.fullmatch(pattern, value)
        if match:
            return target.format(*match.groups())
    return value


def localize_admin(value, key=""):
    if isinstance(value, dict):
        return {name: localize_admin(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [localize_admin(item, key) for item in value]
    if key in {"label", "purpose", "reason", "operation_detail", "connection_detail", "detail", "error", "camera_error", "status_text"}:
        return system_text(value)
    return value
