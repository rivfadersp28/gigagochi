from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.services import local_admin_store
from app.services.local_admin_publish import unexpected_publish_paths


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


def test_local_admin_publish_disabled_without_env(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.local_admin.get_settings",
        lambda: SimpleNamespace(allow_dev_tma_auth=True, admin_publish_enabled=False),
    )

    response = TestClient(app).post("/api/admin/speech/publish", json={"files": []})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ADMIN_PUBLISH_DISABLED"


def test_local_admin_publish_starts_job_when_enabled(monkeypatch) -> None:
    captured = {}

    def fake_start_admin_publish(*, files, settings, commit_message):
        captured["files"] = files
        captured["settings"] = settings
        captured["commit_message"] = commit_message
        return {
            "id": "job-1",
            "status": "running",
            "createdAt": "2026-01-01T00:00:00Z",
            "startedAt": "2026-01-01T00:00:00Z",
            "finishedAt": None,
            "logs": [{"at": "2026-01-01T00:00:00Z", "level": "info", "message": "start"}],
            "error": None,
            "errorCode": None,
            "savedFiles": [],
            "commitSha": None,
        }

    monkeypatch.setattr(
        "app.routers.local_admin.get_settings",
        lambda: SimpleNamespace(
            allow_dev_tma_auth=True,
            admin_publish_enabled=True,
            admin_publish_ssh_target="root@example.test",
        ),
    )
    monkeypatch.setattr(
        "app.routers.local_admin.start_admin_publish",
        fake_start_admin_publish,
    )

    response = TestClient(app).post(
        "/api/admin/speech/publish",
        json={
            "message": "Update admin data",
            "files": [{"id": "story_library", "content": '{"meta":{"version":2}}'}],
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "job-1"
    assert captured["files"] == [
        {"id": "story_library", "content": '{"meta":{"version":2}}'}
    ]
    assert captured["commit_message"] == "Update admin data"


def test_publish_path_filter_rejects_backups_and_unrelated_files() -> None:
    assert unexpected_publish_paths(["backend/data/story_library.json"]) == []
    assert unexpected_publish_paths(
        [
            "backend/data/.admin-backups/story_library.json.bak",
            "shelldon-reference/README.md",
        ]
    ) == [
        "backend/data/.admin-backups/story_library.json.bak",
        "shelldon-reference/README.md",
    ]
