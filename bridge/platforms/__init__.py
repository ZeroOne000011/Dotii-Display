"""Select the active Dotii management-center platform adapter."""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import TYPE_CHECKING

from .base import PlatformAdapter

if TYPE_CHECKING:
    from .macos import MacOSPlatformAdapter
    from .windows import WindowsPlatformAdapter


@lru_cache(maxsize=1)
def current_platform() -> PlatformAdapter:
    if sys.platform == "win32":
        from .windows import WindowsPlatformAdapter

        return WindowsPlatformAdapter()
    if sys.platform == "darwin":
        from .macos import MacOSPlatformAdapter

        return MacOSPlatformAdapter()
    raise RuntimeError(f"Dotii 管理中心暂不支持当前平台：{sys.platform}")


__all__ = ["PlatformAdapter", "current_platform"]
