"""PyPI update check — only imported when the config's `update_check` is on (the default)."""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from importlib import metadata

from toolstart.cli import choose

CACHE = os.path.expanduser("~/.cache/toolstart/update-check.json")
INTERVAL = 6 * 3600  # hit PyPI at most every 6h


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def _save(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(cache, f)


def _cached() -> dict:
    """{"checked": ts, "latest": ver, "skipped": ver}, refreshed from PyPI when older than INTERVAL."""
    try:
        with open(CACHE) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    if time.time() - cache.get("checked", 0) >= INTERVAL:
        cache["checked"] = time.time()  # stamp even on failure so offline doesn't retry every call
        try:
            with urllib.request.urlopen("https://pypi.org/pypi/toolstart/json", timeout=2) as r:
                cache["latest"] = json.load(r)["info"]["version"]
        except Exception:
            pass  # offline / PyPI down: keep the previous answer
        _save(cache)
    return cache


def _upgrade() -> None:
    """Upgrade with whichever installer owns this interpreter."""
    exe = sys.executable
    if f"{os.sep}uv{os.sep}tools{os.sep}" in exe:
        cmd = ["uv", "tool", "upgrade", "toolstart"]
    elif f"{os.sep}pipx{os.sep}" in exe:
        cmd = ["pipx", "upgrade", "toolstart"]
    else:
        cmd = [exe, "-m", "pip", "install", "--upgrade", "toolstart"]
    print(f"toolstart: updating — {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd)
    except FileNotFoundError:
        sys.exit(f"toolstart: update failed, command not found: {cmd[0]}")
    if r.returncode:
        sys.exit(f"toolstart: update failed (exit {r.returncode})")


def offer(restart) -> None:
    """If a newer, not-skipped version exists: install (then `restart()`) | skip this version.
    Escape = ask again next time. Any failure is silent — never block the tool."""
    try:
        current = metadata.version("toolstart")
        cache = _cached()
    except Exception:  # running from a source checkout, unwritable cache dir, ...
        return
    latest = cache.get("latest")
    if not latest or latest == cache.get("skipped") or _ver(latest) <= _ver(current):
        return
    i = choose(f"toolstart {latest} is available (installed: {current}) — esc = later", ["install", "skip this version"])
    if i == 0:
        _upgrade()
        restart()
    elif i == 1:
        cache["skipped"] = latest
        _save(cache)
