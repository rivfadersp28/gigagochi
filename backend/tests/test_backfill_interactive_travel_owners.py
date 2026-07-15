from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.interactive_travel_session_store import InteractiveTravelSessionStore
from scripts.backfill_interactive_travel_owners import (
    BackfillConfigurationError,
    load_mapping,
    main,
    run_backfill,
)


def _result(number: int) -> dict[str, object]:
    return {
        "text": f"Я выполняю решение {number}.",
        "adviceAssessment": "helpful",
        "reaction": "Я продолжаю путь.",
        "reactionTone": "determined",
        "consequence": f"Препятствие {number} пройдено.",
        "outcomeValence": "positive",
    }


def _complete_travel(travel_id: str) -> dict[str, object]:
    parts: list[dict[str, object]] = []
    for number in range(1, 4):
        part: dict[str, object] = {
            "partNumber": number,
            "title": f"Часть {number}",
            "storyText": f"Передо мной препятствие {number}.",
            "challenge": f"Как пройти препятствие {number}?",
            "actionSuggestions": ["Осмотреться"],
            "answer": f"решение {number}",
            "result": _result(number),
        }
        if number > 1:
            part["transition"] = {
                "elapsedHours": 1,
                "summary": f"После препятствия {number - 1} проходит час.",
            }
        parts.append(part)
    return {
        "travelId": travel_id,
        "generatedAt": datetime(2026, 7, 15, 12, tzinfo=UTC).isoformat(),
        "destination": "синтетический город",
        "overallTitle": "Синтетическое путешествие",
        "arcPlan": {"goal": "дойти до башни"},
        "parts": parts,
        "completed": True,
        "outcomeValence": "positive",
    }


def _write_finale(root: Path, travel_id: str, telegram_id: int) -> Path:
    travel_dir = root / travel_id
    travel_dir.mkdir(parents=True)
    path = travel_dir / "finale.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "savedAt": "2026-07-15T12:00:00Z",
                "owner": {
                    "telegramId": telegram_id,
                    "username": None,
                    "firstName": None,
                },
                "travel": _complete_travel(travel_id),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_dry_run_validates_finale_without_creating_owner_store(tmp_path: Path) -> None:
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    owner_store = tmp_path / "private" / "owners.sqlite3"
    _write_finale(generated_root, "interactive-travel-dry-run", 42)

    report, exit_code = run_backfill(
        generated_root=generated_root,
        owner_store_path=owner_store,
    )

    assert exit_code == 0
    assert report.valid_finales == 1
    assert report.eligible == 1
    assert report.registered == 0
    assert not owner_store.exists()
    assert not owner_store.parent.exists()


def test_apply_registers_finale_owner_and_is_idempotent(tmp_path: Path) -> None:
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    owner_store_path = tmp_path / "owners.sqlite3"
    travel_id = "interactive-travel-apply"
    _write_finale(generated_root, travel_id, 42)

    first, first_exit = run_backfill(
        generated_root=generated_root,
        owner_store_path=owner_store_path,
        apply=True,
    )
    second, second_exit = run_backfill(
        generated_root=generated_root,
        owner_store_path=owner_store_path,
        apply=True,
    )

    assert first_exit == second_exit == 0
    assert first.registered == 1
    assert second.registered == 0
    assert second.already_owned == 1
    owner = InteractiveTravelSessionStore(owner_store_path).get_owner(travel_id)
    assert owner is not None
    assert owner.telegram_id == 42


def test_apply_never_overwrites_conflicting_owner(tmp_path: Path) -> None:
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    owner_store_path = tmp_path / "owners.sqlite3"
    travel_id = "interactive-travel-conflict"
    _write_finale(generated_root, travel_id, 42)
    InteractiveTravelSessionStore(owner_store_path).register_owner(travel_id, 84)

    report, exit_code = run_backfill(
        generated_root=generated_root,
        owner_store_path=owner_store_path,
        apply=True,
    )

    assert exit_code == 1
    assert report.conflicts == 1
    assert report.registered == 0
    owner = InteractiveTravelSessionStore(owner_store_path).get_owner(travel_id)
    assert owner is not None
    assert owner.telegram_id == 84


def test_explicit_mapping_registers_unfinished_travel_without_finale(tmp_path: Path) -> None:
    generated_root = tmp_path / "generated"
    travel_id = "interactive-travel-unfinished"
    (generated_root / travel_id).mkdir(parents=True)
    owner_store_path = tmp_path / "owners.sqlite3"

    report, exit_code = run_backfill(
        generated_root=generated_root,
        owner_store_path=owner_store_path,
        mapping={travel_id: 42},
        apply=True,
    )

    assert exit_code == 0
    assert report.mapping_candidates == 1
    assert report.registered == 1
    owner = InteractiveTravelSessionStore(owner_store_path).get_owner(travel_id)
    assert owner is not None
    assert owner.telegram_id == 42


def test_mapping_cannot_override_existing_finale_metadata(tmp_path: Path) -> None:
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    travel_id = "interactive-travel-has-finale"
    _write_finale(generated_root, travel_id, 42)

    with pytest.raises(BackfillConfigurationError):
        run_backfill(
            generated_root=generated_root,
            owner_store_path=tmp_path / "owners.sqlite3",
            mapping={travel_id: 84},
            apply=True,
        )

    assert not (tmp_path / "owners.sqlite3").exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["owner"].update(telegramId="42"),
        lambda payload: payload.update(savedAt="2026-07-15T12:00:00"),
        lambda payload: payload["travel"].update(completed=False, outcomeValence=None),
        lambda payload: payload["travel"].update(travelId="interactive-travel-other"),
    ],
)
def test_invalid_finale_metadata_is_never_used(
    tmp_path: Path,
    mutate,
) -> None:
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    finale_path = _write_finale(generated_root, "interactive-travel-invalid", 42)
    payload = json.loads(finale_path.read_text(encoding="utf-8"))
    mutate(payload)
    finale_path.write_text(json.dumps(payload), encoding="utf-8")
    owner_store_path = tmp_path / "owners.sqlite3"

    report, exit_code = run_backfill(
        generated_root=generated_root,
        owner_store_path=owner_store_path,
        apply=True,
    )

    assert exit_code == 1
    assert report.invalid_finales == 1
    assert report.registered == 0
    assert not owner_store_path.exists()


def test_mapping_loader_rejects_duplicate_keys(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.json"
    mapping.write_text(
        '{"interactive-travel-duplicate": 42, "interactive-travel-duplicate": 84}',
        encoding="utf-8",
    )

    with pytest.raises(BackfillConfigurationError):
        load_mapping(mapping)


def test_cli_prints_only_aggregate_counters_without_identifiers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    travel_id = "interactive-travel-private-marker"
    telegram_id = 987654321
    _write_finale(generated_root, travel_id, telegram_id)

    exit_code = main(
        [
            "--generated-root",
            str(generated_root),
            "--owner-store",
            str(tmp_path / "owners.sqlite3"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"mode": "dry-run"' in captured.out
    assert travel_id not in captured.out
    assert str(telegram_id) not in captured.out
    assert captured.err == ""


def test_cli_invalid_mapping_returns_code_two_without_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    generated_root = tmp_path / "generated"
    (generated_root / "interactive-travel-unfinished").mkdir(parents=True)
    mapping = tmp_path / "mapping.json"
    mapping.write_text('{"interactive-travel-unfinished": "not-an-integer"}', encoding="utf-8")
    owner_store = tmp_path / "owners.sqlite3"

    exit_code = main(
        [
            "--apply",
            "--generated-root",
            str(generated_root),
            "--owner-store",
            str(owner_store),
            "--mapping",
            str(mapping),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "invalid_backfill_input"}
    assert not owner_store.exists()
