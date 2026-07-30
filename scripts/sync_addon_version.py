#!/usr/bin/env python3
"""Sync Home Assistant add-on version from home_delivery/config.yaml."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "home_delivery" / "config.yaml"
MAIN_PY = ROOT / "home_delivery" / "python-service" / "main.py"
API_RUN = ROOT / "home_delivery" / "rootfs" / "etc" / "s6-overlay" / "s6-rc.d" / "api" / "run"
MANIFEST = ROOT / "home_delivery" / "integration" / "home_delivery" / "manifest.json"
PANEL_JS = ROOT / "home_delivery" / "frontend" / "delivery-panel.js"


def read_version() -> str:
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    version = str(data.get("version", "")).strip().strip('"')
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        raise SystemExit(f"Invalid version in {CONFIG}: {version!r}")
    return version


def write_version(version: str) -> None:
    config_text = CONFIG.read_text(encoding="utf-8")
    config_text, n = re.subn(
        r'^version:\s*["\']?[\d.]+["\']?\s*$',
        f'version: "{version}"',
        config_text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        raise SystemExit(f"Could not update version in {CONFIG}")
    CONFIG.write_text(config_text, encoding="utf-8")

    main_text = MAIN_PY.read_text(encoding="utf-8")
    main_text, n = re.subn(
        r'^CODE_VERSION = "[^"]+"',
        f'CODE_VERSION = "{version}"',
        main_text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        raise SystemExit(f"Could not update CODE_VERSION in {MAIN_PY}")
    MAIN_PY.write_text(main_text, encoding="utf-8")

    run_text = API_RUN.read_text(encoding="utf-8")
    run_text, n = re.subn(
        r'HOME DELIVERY API SERVICE v[\d.]+',
        f"HOME DELIVERY API SERVICE v{version}",
        run_text,
        count=1,
    )
    if n != 1:
        raise SystemExit(f"Could not update startup log in {API_RUN}")
    API_RUN.write_text(run_text, encoding="utf-8")

    manifest_text = MANIFEST.read_text(encoding="utf-8")
    manifest_text, n = re.subn(
        r'"version":\s*"[\d.]+"',
        f'"version": "{version}"',
        manifest_text,
        count=1,
    )
    if n != 1:
        raise SystemExit(f"Could not update version in {MANIFEST}")
    MANIFEST.write_text(manifest_text, encoding="utf-8")

    panel_text = PANEL_JS.read_text(encoding="utf-8")
    panel_text, n = re.subn(
        r'const PANEL_VERSION = "[^"]+";',
        f'const PANEL_VERSION = "{version}";',
        panel_text,
        count=1,
    )
    if n != 1:
        raise SystemExit(f"Could not update PANEL_VERSION in {PANEL_JS}")
    PANEL_JS.write_text(panel_text, encoding="utf-8")


def main() -> None:
    if len(sys.argv) == 2:
        write_version(sys.argv[1])
        print(f"Synced add-on version to {sys.argv[1]}")
        return
    print(read_version())


if __name__ == "__main__":
    main()
