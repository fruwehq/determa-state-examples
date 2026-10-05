#!/usr/bin/env python3
"""Verify every README-catalogued example pins the latest State release."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG_ENTRY = re.compile(r"^- \[[^]]+\]\(([^)]+)\)")
EXACT_REQUIREMENT = re.compile(r"^determa-state==([0-9]+\.[0-9]+\.[0-9]+)$")
RELEASE_API = "https://api.github.com/repos/fruwehq/determa-state-spec/releases/latest"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--released-version",
        help="Use this release instead of querying GitHub (for deterministic local checks).",
    )
    parser.add_argument("--candidate-version", choices=["0.3.0"], help="Verify exact public source pins for the approved unreleased candidate.")
    return parser.parse_args()


def latest_released_version() -> str:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "determa-state-examples-release-check",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(RELEASE_API, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError(f"latest release has unexpected tag_name: {tag!r}")
    return tag.removeprefix("v")


def catalog_paths() -> list[Path]:
    lines = (ROOT / "README.md").read_text().splitlines()
    in_catalog = False
    paths: list[Path] = []
    for line in lines:
        if line == "## Example catalog":
            in_catalog = True
            continue
        if in_catalog and line.startswith("## "):
            break
        if in_catalog and (match := CATALOG_ENTRY.match(line)):
            target = match.group(1).rstrip("/")
            if "://" in target or target.startswith(("#", "/")):
                raise ValueError(f"catalog entry must be a relative example folder: {target}")
            path = (ROOT / target).resolve()
            if path.parent != ROOT or not path.is_dir():
                raise ValueError(f"catalog entry is not a top-level example folder: {target}")
            paths.append(path)
    if not paths:
        raise ValueError("README example catalog contains no example folders")
    if len(paths) != len(set(paths)):
        raise ValueError("README example catalog contains a duplicate folder")
    return paths


def python_pins(example: Path) -> list[tuple[Path, str]]:
    pins: list[tuple[Path, str]] = []
    pyproject = example / "pyproject.toml"
    if pyproject.is_file():
        project = tomllib.loads(pyproject.read_text()).get("project", {})
        dependencies = project.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise ValueError(f"{pyproject}: project.dependencies must be a list")
        for dependency in dependencies:
            if isinstance(dependency, str) and dependency.startswith("determa-state"):
                match = EXACT_REQUIREMENT.fullmatch(dependency)
                if match is None:
                    raise ValueError(f"{pyproject}: Determa State must use an exact pin")
                pins.append((pyproject, match.group(1)))

    for requirements in sorted(example.glob("requirements*.*")):
        if requirements.suffix not in {".in", ".txt"}:
            continue
        for raw_line in requirements.read_text().splitlines():
            line = raw_line.strip().removesuffix(" \\")
            if line.startswith("determa-state"):
                match = EXACT_REQUIREMENT.fullmatch(line)
                if match is None:
                    raise ValueError(f"{requirements}: Determa State must use an exact pin")
                pins.append((requirements, match.group(1)))
    return pins


def cargo_pins(example: Path) -> list[tuple[Path, str]]:
    manifest = example / "Cargo.toml"
    if not manifest.is_file():
        return []
    parsed = tomllib.loads(manifest.read_text())
    dependency: Any = parsed.get("dependencies", {}).get("determa-state")
    if dependency is None:
        return []
    version = dependency if isinstance(dependency, str) else dependency.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"=[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError(f"{manifest}: Determa State must use an exact version")
    return [(manifest, version.removeprefix("="))]


def node_pins(example: Path) -> list[tuple[Path, str]]:
    manifest = example / "package.json"
    if not manifest.is_file():
        return []
    parsed = json.loads(manifest.read_text())
    versions = []
    for section in ("dependencies", "devDependencies"):
        value = parsed.get(section, {}).get("determa-state")
        if value is not None:
            if not isinstance(value, str) or not re.fullmatch(
                r"[0-9]+\.[0-9]+\.[0-9]+", value
            ):
                raise ValueError(f"{manifest}: Determa State must use an exact version")
            versions.append((manifest, value))
    return versions


def candidate_pins(example: Path, version: str) -> list[tuple[Path, str]]:
    lock_path = example / "source-lock.json"
    lock = json.loads(lock_path.read_text())
    if lock.get("format") != 1 or lock.get("state_version") != version:
        raise ValueError(f"{lock_path}: invalid candidate version or format")
    repository, commit = lock.get("repository"), lock.get("commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"{lock_path}: exact commit required")
    pyproject = example / "pyproject.toml"
    if pyproject.is_file():
        if repository != "https://github.com/fruwehq/determa-state-python.git":
            raise ValueError(f"{lock_path}: public Python repository required")
        dependencies = tomllib.loads(pyproject.read_text())["project"]["dependencies"]
        expected = f"determa-state @ git+{repository}@{commit}"
        if [d for d in dependencies if d.startswith("determa-state")] != [expected]:
            raise ValueError(f"{pyproject}: candidate source pin mismatch")
        return [(pyproject, version)]
    manifest = example / "Cargo.toml"
    if manifest.is_file():
        if repository != "https://github.com/fruwehq/determa-state-rust":
            raise ValueError(f"{lock_path}: public Rust repository required")
        dependency = tomllib.loads(manifest.read_text())["dependencies"]["determa-state"]
        if (dependency.get("git"), dependency.get("rev"), dependency.get("version")) != (repository, commit, "=" + version):
            raise ValueError(f"{manifest}: candidate source pin mismatch")
        packages = tomllib.loads((example / "Cargo.lock").read_text())["package"]
        state = [p for p in packages if p["name"] == "determa-state"]
        expected = f"git+{repository}?rev={commit}#{commit}"
        if len(state) != 1 or state[0].get("source") != expected or state[0]["version"] != version:
            raise ValueError(f"{manifest}: candidate lockfile mismatch")
        return [(manifest, version)]
    raise ValueError(f"{example}: no candidate dependency manifest")


def main() -> int:
    args = parse_args()
    if args.candidate_version and args.released_version:
        raise ValueError("choose candidate or released validation")
    released = args.candidate_version or args.released_version or latest_released_version()
    failures: list[str] = []
    examples = catalog_paths()
    for example in examples:
        pins = candidate_pins(example, released) if args.candidate_version else python_pins(example) + cargo_pins(example) + node_pins(example)
        if not pins:
            failures.append(f"{example.name}: no exact Determa State dependency pin found")
            continue
        stale = [(path, version) for path, version in pins if version != released]
        if stale:
            rendered = ", ".join(
                f"{path.relative_to(ROOT)}={version}" for path, version in stale
            )
            failures.append(f"{example.name}: expected {released}; found {rendered}")
            continue
        print(f"{example.name}: Determa State {released} ({len(pins)} pins)")

    if failures:
        print("\nRelease freshness check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"All {len(examples)} catalogued examples match {"unreleased candidate" if args.candidate_version else "released"} State {released}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        print(f"Release freshness check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
