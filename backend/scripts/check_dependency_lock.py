from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_PATH = BACKEND_ROOT / "pyproject.toml"
PRODUCTION_LOCK_PATH = BACKEND_ROOT / "requirements.lock"
DEVELOPMENT_LOCK_PATH = BACKEND_ROOT / "requirements-dev.lock"
PIN_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s]+)(.*)$")
HASH_PATTERN = re.compile(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)")


def _normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _logical_lines(path: Path) -> list[str]:
    result: list[str] = []
    buffered = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            buffered += line[:-1].strip() + " "
            continue
        result.append((buffered + line).strip())
        buffered = ""
    if buffered:
        raise RuntimeError(f"unterminated continuation in {path.name}")
    return result


def _read_lock(path: Path, *, expected_include: str | None = None) -> dict[str, str]:
    pins: dict[str, str] = {}
    include_seen = False
    for line in _logical_lines(path):
        if line.startswith("-r "):
            if expected_include is None or line != f"-r {expected_include}" or include_seen:
                raise RuntimeError(f"unexpected include in {path.name}: {line}")
            include_seen = True
            continue
        match = PIN_PATTERN.fullmatch(line)
        if match is None:
            raise RuntimeError(f"lock entry is not an exact pin in {path.name}: {line}")
        raw_name, version, options = match.groups()
        hashes = HASH_PATTERN.findall(options)
        leftover_options = HASH_PATTERN.sub("", options).strip()
        if not hashes or leftover_options:
            raise RuntimeError(f"lock entry must contain only SHA-256 hashes: {raw_name}")
        if len(hashes) != len(set(hashes)):
            raise RuntimeError(f"duplicate hash for {raw_name} in {path.name}")
        name = _normalize_name(raw_name)
        if name in pins:
            raise RuntimeError(f"duplicate package pin for {name} in {path.name}")
        pins[name] = version
    if expected_include is not None and not include_seen:
        raise RuntimeError(f"{path.name} must include {expected_include}")
    return pins


def _assert_requirements_locked(requirements: list[str], pins: dict[str, str]) -> None:
    for raw_requirement in requirements:
        requirement = Requirement(raw_requirement)
        name = _normalize_name(requirement.name)
        version = pins.get(name)
        if version is None:
            raise RuntimeError(f"project dependency is absent from lock: {requirement}")
        if requirement.specifier and not requirement.specifier.contains(version, prereleases=True):
            raise RuntimeError(
                f"locked {name}=={version} does not satisfy project dependency {requirement}"
            )


def main() -> None:
    project = tomllib.loads(PROJECT_PATH.read_text(encoding="utf-8"))["project"]
    optional = project.get("optional-dependencies", {})
    production_pins = _read_lock(PRODUCTION_LOCK_PATH)
    development_additions = _read_lock(
        DEVELOPMENT_LOCK_PATH,
        expected_include=PRODUCTION_LOCK_PATH.name,
    )
    overlap = production_pins.keys() & development_additions.keys()
    if overlap:
        raise RuntimeError(f"development lock duplicates production pins: {sorted(overlap)}")

    _assert_requirements_locked(
        [*project["dependencies"], *optional.get("litellm", [])],
        production_pins,
    )
    _assert_requirements_locked(
        optional.get("dev", []),
        production_pins | development_additions,
    )
    print(
        "dependency locks valid: "
        f"production={len(production_pins)} development={len(development_additions)}"
    )


if __name__ == "__main__":
    main()
