from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.services import local_admin_store


def _seed_admin_files(root) -> None:
    (root / "age_speech_examples").mkdir(parents=True)
    (root / "world_descriptions").mkdir(parents=True)
    (root / "external_character_sources").mkdir(parents=True)
    for path in (
        "speech_runtime.json",
        "story_library.json",
        "story_constructor.json",
        "travel_story_templates.json",
        "age_speech_examples/creature_phrases_dataset.json",
        "world_descriptions/world_descriptions_dataset.json",
    ):
        (root / path).write_text('{"meta":{"version":1}}\n', encoding="utf-8")
    (root / "external_character_sources/fragments.jsonl").write_text(
        '{"id":"a","text":"seed","source_url":"https://example.com"}\n',
        encoding="utf-8",
    )


def test_local_admin_disabled_without_dev_auth(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(local_admin_store, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        "app.routers.local_admin.get_settings",
        lambda: SimpleNamespace(allow_dev_tma_auth=False),
    )

    response = TestClient(app).get("/api/admin/speech")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "LOCAL_ADMIN_DISABLED"


def test_local_admin_reads_managed_files(monkeypatch, tmp_path) -> None:
    _seed_admin_files(tmp_path)
    monkeypatch.setattr(local_admin_store, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        "app.routers.local_admin.get_settings",
        lambda: SimpleNamespace(allow_dev_tma_auth=True),
    )

    response = TestClient(app).get("/api/admin/speech")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "local"
    assert [item["id"] for item in payload["files"]][:2] == [
        "speech_runtime",
        "story_library",
    ]
    assert payload["files"][0]["content"].startswith("{")


def test_local_admin_saves_json_and_makes_backup(monkeypatch, tmp_path) -> None:
    _seed_admin_files(tmp_path)
    monkeypatch.setattr(local_admin_store, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        "app.routers.local_admin.get_settings",
        lambda: SimpleNamespace(allow_dev_tma_auth=True),
    )

    response = TestClient(app).put(
        "/api/admin/speech",
        json={
            "files": [
                {
                    "id": "speech_runtime",
                    "content": '{"personaContract":"Пиши короче."}',
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["saved"] is True
    assert payload["files"][0]["backupPath"].startswith(".admin-backups/")
    saved = (tmp_path / "speech_runtime.json").read_text(encoding="utf-8")
    assert '"personaContract": "Пиши короче."' in saved


def test_local_admin_rejects_invalid_json(monkeypatch, tmp_path) -> None:
    _seed_admin_files(tmp_path)
    monkeypatch.setattr(local_admin_store, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        "app.routers.local_admin.get_settings",
        lambda: SimpleNamespace(allow_dev_tma_auth=True),
    )

    response = TestClient(app).put(
        "/api/admin/speech",
        json={"files": [{"id": "speech_runtime", "content": "{"}]},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["errors"]["speech_runtime"]
