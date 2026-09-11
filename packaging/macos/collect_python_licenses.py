"""Collect redistributable Python runtime and wheel license files."""

from __future__ import annotations

import email
import shutil
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: collect_python_licenses.py OUTPUT_DIRECTORY")
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)

    python_root = Path(sys.executable).resolve().parent.parent
    runtime_license = python_root / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"
    if not runtime_license.is_file():
        raise SystemExit("Python runtime license is missing")
    shutil.copy2(runtime_license, output / "Python-LICENSE.txt")

    site_packages = python_root / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    packages: list[str] = []
    for metadata_dir in sorted(site_packages.glob("*.dist-info")):
        metadata_file = metadata_dir / "METADATA"
        if not metadata_file.is_file():
            continue
        metadata = email.message_from_bytes(metadata_file.read_bytes())
        name = metadata.get("Name", metadata_dir.stem)
        version = metadata.get("Version", "unknown")
        license_expression = metadata.get("License-Expression") or metadata.get("License") or "see bundled files"
        license_summary = " ".join(license_expression.split())
        if len(license_summary) > 160:
            license_summary = "see bundled files"
        packages.append(f"{name} {version} | {license_summary}")

        candidates = [
            path for path in metadata_dir.rglob("*")
            if path.is_file() and (
                path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))
                or "licenses" in {part.lower() for part in path.parts}
            )
        ]
        for source in candidates:
            relative = source.relative_to(metadata_dir)
            destination = output / "python-packages" / metadata_dir.stem / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    (output / "PYTHON-PACKAGES.txt").write_text("\n".join(packages) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
