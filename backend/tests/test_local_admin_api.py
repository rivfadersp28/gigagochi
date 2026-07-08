from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.services import local_admin_store
from app.services.local_admin_publish import (
    AdminPublishError,
    AdminPublishJob,
    _deploy_admin_data_on_hetzner,
    _parse_push_command_output,
    unexpected_publish_paths,
)

DATA_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "data"


def _seed_admin_files(root) -> None:
    (root / "age_speech_examples").mkdir(parents=True)
    (root / "world_descriptions").mkdir(parents=True)
    for path in (
        "story_library.json",
        "story_constructor.json",
        "travel_story_templates.json",
        "age_speech_examples/creature_phrases_dataset.json",
        "world_descriptions/world_descriptions_dataset.json",
    ):
        (root / path).write_text('{"meta":{"version":1}}\n', encoding="utf-8")
    for path in ("speech_runtime.json", "tone_runtime.json", "character_bible_template.json"):
        (root / path).write_text(
            (DATA_FIXTURE_ROOT / path).read_text(encoding="utf-8"),
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
    assert payload["dialogue"]["modifiers"]
    assert payload["dialogue"]["collections"]
    assert [item["id"] for item in payload["files"]][:2] == [
        "speech_runtime",
        "tone_runtime",
    ]
    assert payload["files"][0]["content"].startswith("{")


def test_local_admin_syncs_before_reading_manifest(monkeypatch, tmp_path) -> None:
    _seed_admin_files(tmp_path)
    captured = {}
    monkeypatch.setattr(local_admin_store, "DATA_ROOT", tmp_path)
    settings = SimpleNamespace(
        allow_dev_tma_auth=True,
        admin_sync_from_server_enabled=True,
    )
    monkeypatch.setattr("app.routers.local_admin.get_settings", lambda: settings)

    def fake_sync_admin_files_from_server(sync_settings):
        captured["settings"] = sync_settings
        return {
            "status": "synced",
            "message": "ok",
            "serverCommit": "abc123",
            "updatedAt": "2026-01-01T00:00:00Z",
        }

    monkeypatch.setattr(
        "app.routers.local_admin.sync_admin_files_from_server",
        fake_sync_admin_files_from_server,
    )

    response = TestClient(app).get("/api/admin/speech")

    assert response.status_code == 200
    payload = response.json()
    assert captured["settings"] is settings
    assert payload["sync"]["status"] == "synced"
    assert payload["sync"]["serverCommit"] == "abc123"


def test_local_admin_reads_production_manifest(monkeypatch) -> None:
    captured = {}
    settings = SimpleNamespace(allow_dev_tma_auth=True, admin_publish_enabled=True)
    monkeypatch.setattr("app.routers.local_admin.get_settings", lambda: settings)

    def fake_read_admin_manifest_from_server(sync_settings, *, deploy_enabled, deploy_message):
        captured["settings"] = sync_settings
        captured["deploy_enabled"] = deploy_enabled
        captured["deploy_message"] = deploy_message
        return {
            "generatedAt": "2026-01-01T00:00:00Z",
            "mode": "production",
            "files": [],
            "dialogue": {"modifiers": [], "collections": []},
            "sync": {
                "status": "production",
                "message": "ok",
                "serverCommit": "abc123",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "deploy": {"enabled": True, "message": deploy_message},
        }

    monkeypatch.setattr(
        "app.routers.local_admin.read_admin_manifest_from_server",
        fake_read_admin_manifest_from_server,
    )

    response = TestClient(app).get("/api/admin/speech?source=production")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "production"
    assert payload["sync"]["status"] == "production"
    assert captured["settings"] is settings
    assert captured["deploy_enabled"] is True
    assert "Hetzner" in captured["deploy_message"]


def test_local_admin_sync_conflict_returns_local_manifest(monkeypatch, tmp_path) -> None:
    _seed_admin_files(tmp_path)
    monkeypatch.setattr(local_admin_store, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        "app.routers.local_admin.get_settings",
        lambda: SimpleNamespace(
            allow_dev_tma_auth=True,
            admin_sync_from_server_enabled=True,
        ),
    )

    def fake_sync_admin_files_from_server(_settings):
        raise AdminPublishError("ADMIN_SYNC_LOCAL_DIRTY", "dirty")

    monkeypatch.setattr(
        "app.routers.local_admin.sync_admin_files_from_server",
        fake_sync_admin_files_from_server,
    )

    response = TestClient(app).get("/api/admin/speech")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "local"
    assert payload["files"]
    assert payload["sync"]["status"] == "local_dirty"
    assert "незадеплоенные изменения" in payload["sync"]["message"]


def test_local_admin_saves_json_and_makes_backup(monkeypatch, tmp_path) -> None:
    _seed_admin_files(tmp_path)
    monkeypatch.setattr(local_admin_store, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        "app.routers.local_admin.get_settings",
        lambda: SimpleNamespace(allow_dev_tma_auth=True),
    )

    runtime = json.loads((tmp_path / "speech_runtime.json").read_text(encoding="utf-8"))
    runtime["surfacePrompts"]["chat"] = "Пиши короче."

    response = TestClient(app).put(
        "/api/admin/speech",
        json={
            "files": [
                {
                    "id": "speech_runtime",
                    "content": json.dumps(runtime, ensure_ascii=False),
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["saved"] is True
    assert payload["files"][0]["backupPath"].startswith(".admin-backups/")
    saved = (tmp_path / "speech_runtime.json").read_text(encoding="utf-8")
    assert '"chat": "Пиши короче."' in saved


def test_local_admin_rejects_direct_production_save(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.local_admin.get_settings",
        lambda: SimpleNamespace(allow_dev_tma_auth=True),
    )

    response = TestClient(app).put(
        "/api/admin/speech?source=production",
        json={"files": [{"id": "speech_runtime", "content": "{}"}]},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ADMIN_PRODUCTION_DIRECT_SAVE_DISABLED"


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


def test_admin_data_deploy_uses_no_build(monkeypatch) -> None:
    captured = {}

    def fake_run_logged_command(job, args, *, cwd, timeout):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["timeout"] = timeout

    monkeypatch.setattr(
        "app.services.local_admin_publish._run_logged_command",
        fake_run_logged_command,
    )

    _deploy_admin_data_on_hetzner(
        AdminPublishJob(id="job-1"),
        SimpleNamespace(
            admin_publish_ssh_target="root@example.test",
            admin_publish_ssh_key_path=None,
            admin_publish_remote_path="/opt/gigagochi",
            admin_publish_git_remote="origin",
            admin_publish_git_branch="main",
        ),
        120,
    )

    remote_command = captured["args"][-1]
    assert "git pull --ff-only origin main" in remote_command
    assert "up -d --no-build backend bot" in remote_command
    assert " --build" not in remote_command


def test_local_admin_push_endpoints_are_removed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.local_admin.get_settings",
        lambda: SimpleNamespace(allow_dev_tma_auth=True),
    )

    client = TestClient(app)

    assert client.get("/api/admin/push/status").status_code == 404
    assert client.post("/api/admin/push/send", json={}).status_code == 404
    assert client.post("/api/admin/push/send-all", json={}).status_code == 404


def test_production_push_output_parser_ignores_prompt_debug() -> None:
    parsed = _parse_push_command_output(
        "\n".join(
            [
                "=== AI chat prompt: pet_reply/push ===",
                '{"label":"debug","messages":[]}',
                "=== End AI chat prompt ===",
                '{"ok": true, "result": {"sent": true, "reply": "hi"}}',
            ]
        )
    )

    assert parsed == {"ok": True, "result": {"sent": True, "reply": "hi"}}


def test_publish_path_filter_rejects_backups_and_unrelated_files() -> None:
    assert unexpected_publish_paths(["backend/data/story_library.json"]) == []
    assert unexpected_publish_paths(["backend/data/tone_runtime.json"]) == []
    assert unexpected_publish_paths(
        [
            "backend/data/.admin-backups/story_library.json.bak",
            "shelldon-reference/README.md",
        ]
    ) == [
        "backend/data/.admin-backups/story_library.json.bak",
        "shelldon-reference/README.md",
    ]
