"""Install the exact public unreleased candidate after hashed dependencies."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path


def run(*arguments: str) -> str:
    return subprocess.check_output(arguments, text=True).strip()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    pin = json.loads((root / "source-lock.json").read_text())
    if (
        pin["format"] != 1
        or pin["repository"] != ("https://github.com/fruwehq/determa-state-python.git")
        or not re.fullmatch(r"[0-9a-f]{40}", pin["commit"])
    ):
        raise SystemExit("invalid public candidate source pin")
    source = root / ".candidate-source"
    if not source.exists():
        subprocess.check_call(["git", "clone", pin["repository"], str(source)])
    if run("git", "-C", str(source), "remote", "get-url", "origin") != pin["repository"]:
        raise SystemExit("candidate checkout belongs to another repository")
    if run("git", "-C", str(source), "status", "--porcelain"):
        raise SystemExit("candidate checkout contains local changes")
    subprocess.check_call(["git", "-C", str(source), "fetch", "origin", pin["commit"]])
    subprocess.check_call(["git", "-C", str(source), "checkout", "--detach", pin["commit"]])
    if run("git", "-C", str(source), "rev-parse", "HEAD") != pin["commit"]:
        raise SystemExit("candidate checkout does not match the exact public pin")
    metadata = tomllib.loads((source / "pyproject.toml").read_text())
    if metadata["tool"]["hatch"]["version"]["path"] != "src/determa/state/__about__.py":
        raise SystemExit("candidate package version source differs from the reviewed contract")
    version = re.search(
        r'^__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"',
        (source / "src/determa/state/__about__.py").read_text(),
        re.MULTILINE,
    )
    if version is None or version.group(1) != pin["state_version"]:
        raise SystemExit("candidate package version differs from the source lock")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--no-build-isolation", "--no-deps", str(source)]
    )


if __name__ == "__main__":
    main()
