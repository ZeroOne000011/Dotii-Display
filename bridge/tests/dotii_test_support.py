"""Keep disposable test data inside the project, including on a fresh clone."""

import tempfile
from pathlib import Path


def temporary_directory() -> tempfile.TemporaryDirectory:
    root = Path(__file__).resolve().parents[2] / ".codex"
    root.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)
