from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_settings import SettingsError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings  # noqa: E402
from app.schemas import InteractiveTravelState  # noqa: E402
from app.services.interactive_travel_finale_service import (  # noqa: E402
    FINALE_FILENAME,
    GENERATED_ROOT,
)
from app.services.interactive_travel_session_store import (  # noqa: E402
    DEFAULT_INTERACTIVE_TRAVEL_MAX_RECORDS,
    DEFAULT_INTERACTIVE_TRAVEL_RETENTION,
    InteractiveTravelSessionCancelledError,
    InteractiveTravelSessionCapacityError,
    InteractiveTravelSessionOwnerMismatchError,
    InteractiveTravelSessionStore,
)

TRAVEL_ID_PATTERN = re.compile(r"interactive-travel-[A-Za-z0-9_-]+")
MAX_FINALE_BYTES = 1_048_576
MAX_MAPPING_BYTES = 4_194_304
MAX_TELEGRAM_ID = 2**63 - 1
OWNER_TABLE = "interactive_travel_owners"


class BackfillConfigurationError(RuntimeError):
    """Raised for invalid input or a store that cannot be inspected safely."""


@dataclass(frozen=True, slots=True)
class OwnerCandidate:
    travel_id: str
    telegram_id: int


@dataclass(frozen=True, slots=True)
class ExistingOwner:
    telegram_id: int
    cancelled: bool


@dataclass(slots=True)
class BackfillReport:
    scanned_directories: int = 0
    valid_finales: int = 0
    mapping_candidates: int = 0
    invalid_finales: int = 0
    unresolved_directories: int = 0
    unsafe_entries: int = 0
    eligible: int = 0
    already_owned: int = 0
    conflicts: int = 0
    cancelled: int = 0
    registered: int = 0
    write_errors: int = 0


def _telegram_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("telegram ID must be an integer")
    if value <= 0 or value > MAX_TELEGRAM_ID:
        raise ValueError("telegram ID is outside the supported range")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_bounded_json(path: Path, *, max_bytes: int) -> Any:
    if path.is_symlink():
        raise ValueError("symbolic links are not accepted")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise ValueError("JSON input must be a non-empty regular file")
        if metadata.st_size > max_bytes:
            raise ValueError("JSON input is too large")
        with os.fdopen(descriptor, encoding="utf-8") as source:
            descriptor = -1
            content = source.read(max_bytes + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content.encode("utf-8")) > max_bytes:
        raise ValueError("JSON input is too large")
    return json.loads(content, object_pairs_hook=_reject_duplicate_keys)


def _aware_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("savedAt must be a timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("savedAt must include a timezone")
    return parsed.astimezone(UTC)


def _owner_from_finale(path: Path, *, expected_travel_id: str) -> int:
    payload = _read_bounded_json(path, max_bytes=MAX_FINALE_BYTES)
    if not isinstance(payload, dict):
        raise ValueError("finale metadata must be an object")
    schema_version = payload.get("schemaVersion")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise ValueError("unsupported finale schema")
    _aware_timestamp(payload.get("savedAt"))
    owner = payload.get("owner")
    if not isinstance(owner, dict):
        raise ValueError("finale owner is missing")
    for optional_name in ("username", "firstName"):
        optional_value = owner.get(optional_name)
        if optional_value is not None and not isinstance(optional_value, str):
            raise ValueError("invalid optional owner metadata")
    telegram_id = _telegram_id(owner.get("telegramId"))
    travel = InteractiveTravelState.model_validate(payload.get("travel"))
    if travel.travelId != expected_travel_id or not travel.completed:
        raise ValueError("finale travel identity or completion state is invalid")
    return telegram_id


def load_mapping(path: Path | None) -> dict[str, int]:
    if path is None:
        return {}
    try:
        payload = _read_bounded_json(path, max_bytes=MAX_MAPPING_BYTES)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise BackfillConfigurationError("invalid mapping file") from exc
    if not isinstance(payload, dict):
        raise BackfillConfigurationError("mapping must be a JSON object")
    result: dict[str, int] = {}
    try:
        for travel_id, telegram_id in payload.items():
            if not isinstance(travel_id, str) or TRAVEL_ID_PATTERN.fullmatch(travel_id) is None:
                raise ValueError("invalid travel ID")
            result[travel_id] = _telegram_id(telegram_id)
    except ValueError as exc:
        raise BackfillConfigurationError("invalid mapping entry") from exc
    return result


def _validate_mapping_targets(generated_root: Path, mapping: dict[str, int]) -> None:
    for travel_id in mapping:
        travel_dir = generated_root / travel_id
        if travel_dir.is_symlink() or not travel_dir.is_dir():
            raise BackfillConfigurationError("mapping target is not a generated travel directory")
        if os.path.lexists(travel_dir / FINALE_FILENAME):
            raise BackfillConfigurationError("mapping target already has finale metadata")


def _scan_candidates(
    generated_root: Path,
    mapping: dict[str, int],
) -> tuple[BackfillReport, list[OwnerCandidate]]:
    if generated_root.is_symlink() or not generated_root.is_dir():
        raise BackfillConfigurationError("generated root is not a regular directory")
    _validate_mapping_targets(generated_root, mapping)
    report = BackfillReport()
    candidates: list[OwnerCandidate] = []
    try:
        entries = sorted(generated_root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise BackfillConfigurationError("generated root cannot be read") from exc
    for travel_dir in entries:
        if TRAVEL_ID_PATTERN.fullmatch(travel_dir.name) is None:
            continue
        report.scanned_directories += 1
        if travel_dir.is_symlink() or not travel_dir.is_dir():
            report.unsafe_entries += 1
            continue
        finale_path = travel_dir / FINALE_FILENAME
        if os.path.lexists(finale_path):
            try:
                telegram_id = _owner_from_finale(
                    finale_path,
                    expected_travel_id=travel_dir.name,
                )
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                report.invalid_finales += 1
                continue
            report.valid_finales += 1
            candidates.append(OwnerCandidate(travel_dir.name, telegram_id))
            continue
        telegram_id = mapping.get(travel_dir.name)
        if telegram_id is None:
            report.unresolved_directories += 1
            continue
        report.mapping_candidates += 1
        candidates.append(OwnerCandidate(travel_dir.name, telegram_id))
    return report, candidates


def _read_existing_owners(
    store_path: Path,
    candidates: list[OwnerCandidate],
) -> dict[str, ExistingOwner]:
    if store_path.is_symlink():
        raise BackfillConfigurationError("owner store cannot be a symbolic link")
    if not store_path.exists():
        return {}
    if not store_path.is_file():
        raise BackfillConfigurationError("owner store is not a regular file")
    try:
        with sqlite3.connect(f"{store_path.absolute().as_uri()}?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (OWNER_TABLE,),
            ).fetchone()
            if table is None:
                raise BackfillConfigurationError("owner store schema is missing")
            result: dict[str, ExistingOwner] = {}
            for candidate in candidates:
                row = connection.execute(
                    f"SELECT telegram_id, cancelled_at FROM {OWNER_TABLE} WHERE travel_id = ?",
                    (candidate.travel_id,),
                ).fetchone()
                if row is not None:
                    result[candidate.travel_id] = ExistingOwner(
                        telegram_id=_telegram_id(row[0]),
                        cancelled=row[1] is not None,
                    )
            return result
    except BackfillConfigurationError:
        raise
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        raise BackfillConfigurationError("owner store cannot be inspected") from exc


def _exit_code(report: BackfillReport) -> int:
    incomplete = (
        report.invalid_finales
        + report.unresolved_directories
        + report.unsafe_entries
        + report.conflicts
        + report.cancelled
        + report.write_errors
    )
    return 1 if incomplete else 0


def run_backfill(
    *,
    generated_root: Path,
    owner_store_path: Path,
    mapping: dict[str, int] | None = None,
    apply: bool = False,
    retention: timedelta = DEFAULT_INTERACTIVE_TRAVEL_RETENTION,
    max_records: int = DEFAULT_INTERACTIVE_TRAVEL_MAX_RECORDS,
) -> tuple[BackfillReport, int]:
    resolved_mapping = mapping or {}
    report, candidates = _scan_candidates(generated_root, resolved_mapping)
    existing = _read_existing_owners(owner_store_path, candidates)
    eligible: list[OwnerCandidate] = []
    for candidate in candidates:
        owner = existing.get(candidate.travel_id)
        if owner is None:
            eligible.append(candidate)
            continue
        if owner.telegram_id != candidate.telegram_id:
            report.conflicts += 1
        elif owner.cancelled:
            report.cancelled += 1
        else:
            report.already_owned += 1
    report.eligible = len(eligible)
    if apply and eligible:
        try:
            store = InteractiveTravelSessionStore(
                owner_store_path,
                retention=retention,
                max_records=max_records,
            )
        except (OSError, sqlite3.DatabaseError, ValueError):
            report.write_errors += len(eligible)
            return report, _exit_code(report)
        for candidate in eligible:
            try:
                store.register_owner(candidate.travel_id, candidate.telegram_id)
                report.registered += 1
            except InteractiveTravelSessionOwnerMismatchError:
                report.conflicts += 1
            except InteractiveTravelSessionCancelledError:
                report.cancelled += 1
            except (InteractiveTravelSessionCapacityError, OSError, sqlite3.DatabaseError):
                report.write_errors += 1
    return report, _exit_code(report)


def _absolute_backend_path(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else BACKEND_ROOT / expanded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill interactive-travel owners from validated finale metadata. "
            "Dry-run is the default."
        ),
        epilog=(
            "Exit codes: 0 = complete scan, 1 = unresolved/conflicting/invalid records or "
            "write failures, 2 = invalid configuration or input."
        ),
    )
    parser.add_argument("--apply", action="store_true", help="persist eligible owner bindings")
    parser.add_argument(
        "--mapping",
        type=Path,
        help="JSON object mapping unfinished travel IDs to numeric Telegram IDs",
    )
    parser.add_argument("--generated-root", type=Path, help="override generated-assets root")
    parser.add_argument("--owner-store", type=Path, help="override owner SQLite path")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
        generated_root = _absolute_backend_path(args.generated_root or GENERATED_ROOT)
        configured_store = args.owner_store or Path(settings.interactive_travel_owner_store_path)
        owner_store_path = _absolute_backend_path(configured_store)
        mapping_path = _absolute_backend_path(args.mapping) if args.mapping is not None else None
        mapping = load_mapping(mapping_path)
        report, exit_code = run_backfill(
            generated_root=generated_root,
            owner_store_path=owner_store_path,
            mapping=mapping,
            apply=args.apply,
            retention=timedelta(seconds=settings.interactive_travel_owner_retention_seconds),
            max_records=settings.interactive_travel_owner_max_records,
        )
    except (BackfillConfigurationError, SettingsError, ValidationError, TypeError, ValueError):
        print(json.dumps({"error": "invalid_backfill_input"}), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"mode": "apply" if args.apply else "dry-run", **asdict(report)},
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
