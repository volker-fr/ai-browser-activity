#!/usr/bin/env python3
"""Check local prerequisites without running validation or tests."""

import importlib.util
import platform
import shutil
import sys

REQUIRED_MODULES = (
    "html", "json", "math", "os", "re", "socket", "sys", "tempfile", "time",
    "secrets", "datetime", "http.server", "pathlib", "threading", "urllib.parse",
)


def main():
    problems = []
    if sys.version_info < (3, 10):
        problems.append(f"Python 3.10+ required; found {platform.python_version()}.")

    missing = [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]
    if missing:
        problems.append("Python standard-library modules missing: " + ", ".join(missing) + ".")

    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1

    print(f"Runtime prerequisites: OK (Python {platform.python_version()}, standard library)")
    if shutil.which("node"):
        print("Optional test prerequisite: Node.js available (make test uses it).")
    else:
        print("Optional test prerequisite: Node.js not found (needed only for make test).")
    if platform.system() == "Linux":
        systemctl = bool(shutil.which("systemctl"))
        journalctl = bool(shutil.which("journalctl"))
        print("Optional Linux systemd commands: " + ("systemctl" if systemctl else "systemctl missing") + ", " + ("journalctl" if journalctl else "journalctl missing") + ".")
    else:
        print("Optional Linux systemd service setup: unavailable on this platform.")
    print("Browser and userscript-extension installation are not checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
