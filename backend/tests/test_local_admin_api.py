from __future__ import annotations

import json
import multiprocessing
import os
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import local_admin_publish, local_admin_store
from app.services.local_admin_publish import (
    MAX_RETAINED_ADMIN_PUBLISH_JOBS,
    AdminPublishError,
    AdminPublishJob,
    _check_health,
    _deploy_admin_data_on_hetzner,
    _run_logged_command,
    unexpected_publish_paths,
)

DATA_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "data"


def test_admin_publish_registry_prunes_old_terminal_jobs(monkeypatch) -> None:
    jobs: dict[str, AdminPublishJob] = {}
    for index in range(MAX_RETAINED_ADMIN_PUBLISH_JOBS + 8):
        job = AdminPublishJob(id=f"terminal-{index:03d}")
        job.status = "succeeded"
        job.finished_at = f"2026-07-15T12:{index:02d}:00Z"
        jobs[job.id] = job
    active = AdminPublishJob(id="active-job", status="running")
    jobs[active.id] = active
    monkeypatch.setattr(local_admin_publish, "_jobs", jobs)

    local_admin_publish._prune_terminal_jobs_locked(reserve_slots=1)

    assert active.id in jobs
    retained_terminal = [job for job in jobs.values() if job.status == "succeeded"]
    assert len(retained_terminal) == MAX_RETAINED_ADMIN_PUBLISH_JOBS - 1
    assert "terminal-000" not in jobs
    assert f"terminal-{MAX_RETAINED_ADMIN_PUBLISH_JOBS + 7:03d}" in jobs


def _save_story_library_in_process(
    data_root: str,
    version: int,
    ready_queue,
    start_event,
    result_queue,
) -> None:
    local_admin_store.DATA_ROOT = Path(data_root)
    ready_queue.put(version)
    if not start_event.wait(timeout=10):
        result_queue.put({"error": "start timeout"})
        return
    try:
        result = local_admin_store.save_admin_files(
            [
                {
                    "id": "story_library",
                    "content": json.dumps({"meta": {"version": version}}),
                }
            ]
        )
    except Exception as exc:  # pragma: no cover - asserted in the parent process
        result_queue.put({"error": f"{type(exc).__name__}: {exc}"})
        return
    result_queue.put(result)


def _seed_admin_files(root) -> None:
    (root / "age_speech_examples").mkdir(parents=True)
    (root / "world_descriptions").mkdir(parents=True)
    for path in (
        "story_library.json",
        "story_constructor.json",
        "age_speech_examples/creature_phrases_dataset.json",
        "world_descriptions/world_descriptions_dataset.json",
    ):
        (root / path).write_text('{"meta":{"version":1}}\n', encoding="utf-8")
    for path in (
        "speech_runtime.json",
        "tone_runtime.json",
        "lore_runtime.json",
        "character_bible_template.json",
    ):
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
    assert "dialogue" not in payload
    assert {item["format"] for item in payload["files"]} == {"json"}
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


def test_local_admin_fast_saves_never_reuse_backup(monkeypatch, tmp_path) -> None:
    _seed_admin_files(tmp_path)
    monkeypatch.setattr(local_admin_store, "DATA_ROOT", tmp_path)
    real_datetime = local_admin_store.datetime
    fixed_time = real_datetime(2026, 1, 1, tzinfo=local_admin_store.UTC)

    class FixedDateTime:
        @staticmethod
        def now(_timezone):
            return fixed_time

    monkeypatch.setattr(local_admin_store, "datetime", FixedDateTime)

    first = local_admin_store.save_admin_files(
        [{"id": "story_library", "content": '{"meta":{"version":2}}'}]
    )
    second = local_admin_store.save_admin_files(
        [{"id": "story_library", "content": '{"meta":{"version":3}}'}]
    )

    first_backup = tmp_path / first["files"][0]["backupPath"]
    second_backup = tmp_path / second["files"][0]["backupPath"]
    assert first_backup != second_backup
    assert json.loads(first_backup.read_text(encoding="utf-8"))["meta"]["version"] == 1
    assert json.loads(second_backup.read_text(encoding="utf-8"))["meta"]["version"] == 2


def test_local_admin_backup_collision_never_overwrites_existing_file(
    monkeypatch,
    tmp_path,
) -> None:
    _seed_admin_files(tmp_path)
    monkeypatch.setattr(local_admin_store, "DATA_ROOT", tmp_path)
    collision = tmp_path / ".admin-backups" / "collision.bak"
    collision.parent.mkdir()
    collision.write_bytes(b"keep me")
    monkeypatch.setattr(local_admin_store, "_backup_path", lambda _path: collision)

    with pytest.raises(RuntimeError, match="unique admin backup"):
        local_admin_store._create_backup(tmp_path / "story_library.json", b"replacement")

    assert collision.read_bytes() == b"keep me"


def test_local_admin_temp_collision_never_overwrites_or_deletes_existing_file(
    monkeypatch,
    tmp_path,
) -> None:
    target = tmp_path / "story_library.json"
    target.write_bytes(b"original")
    fixed_uuid = SimpleNamespace(hex="fixed")
    temporary = tmp_path / f".story_library.json.{os.getpid()}.fixed.tmp"
    temporary.write_bytes(b"other writer")
    monkeypatch.setattr(local_admin_store.uuid, "uuid4", lambda: fixed_uuid)

    with pytest.raises(FileExistsError):
        local_admin_store._atomic_write(target, '{"meta":{"version":2}}')

    assert target.read_bytes() == b"original"
    assert temporary.read_bytes() == b"other writer"


def test_local_admin_saves_are_serialized_across_threads(monkeypatch, tmp_path) -> None:
    _seed_admin_files(tmp_path)
    monkeypatch.setattr(local_admin_store, "DATA_ROOT", tmp_path)
    real_atomic_write = local_admin_store._atomic_write
    counter_lock = threading.Lock()
    start = threading.Barrier(8)
    active_writes = 0
    max_active_writes = 0

    def observed_atomic_write(path: Path, content: str) -> None:
        nonlocal active_writes, max_active_writes
        with counter_lock:
            active_writes += 1
            max_active_writes = max(max_active_writes, active_writes)
        try:
            time.sleep(0.01)
            real_atomic_write(path, content)
        finally:
            with counter_lock:
                active_writes -= 1

    def save(version: int) -> dict:
        start.wait(timeout=10)
        return local_admin_store.save_admin_files(
            [
                {
                    "id": "story_library",
                    "content": json.dumps({"meta": {"version": version}}),
                }
            ]
        )

    monkeypatch.setattr(local_admin_store, "_atomic_write", observed_atomic_write)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(save, range(10, 18)))

    assert max_active_writes == 1
    assert all(result["saved"] for result in results)
    assert len({result["files"][0]["backupPath"] for result in results}) == 8
    assert json.loads((tmp_path / "story_library.json").read_text(encoding="utf-8"))["meta"][
        "version"
    ] in range(10, 18)
    assert not list(tmp_path.glob(".*.tmp"))


def test_local_admin_saves_are_serialized_across_processes(monkeypatch, tmp_path) -> None:
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("requires POSIX fork and flock")
    _seed_admin_files(tmp_path)
    monkeypatch.setattr(local_admin_store, "DATA_ROOT", tmp_path)
    context = multiprocessing.get_context("fork")
    ready_queue = context.Queue()
    result_queue = context.Queue()
    start_event = context.Event()
    processes = [
        context.Process(
            target=_save_story_library_in_process,
            args=(str(tmp_path), version, ready_queue, start_event, result_queue),
        )
        for version in range(20, 24)
    ]

    results = []
    try:
        for process in processes:
            process.start()
        for _process in processes:
            ready_queue.get(timeout=10)
        start_event.set()
        results = [result_queue.get(timeout=20) for _process in processes]
    finally:
        start_event.set()
        for process in processes:
            process.join(timeout=20)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert all(process.exitcode == 0 for process in processes)
    assert all("error" not in result for result in results)
    assert len({result["files"][0]["backupPath"] for result in results}) == 4
    backups = list((tmp_path / ".admin-backups").glob("story_library.json.*.bak"))
    assert len(backups) == 4
    backup_versions = {
        json.loads(path.read_text(encoding="utf-8"))["meta"]["version"] for path in backups
    }
    assert len(backup_versions) == 4
    final_version = json.loads((tmp_path / "story_library.json").read_text(encoding="utf-8"))[
        "meta"
    ]["version"]
    assert final_version in range(20, 24)
    assert not list(tmp_path.glob(".*.tmp"))


def test_local_admin_batch_save_rolls_back_on_write_error(monkeypatch, tmp_path) -> None:
    _seed_admin_files(tmp_path)
    monkeypatch.setattr(local_admin_store, "DATA_ROOT", tmp_path)
    story_library = tmp_path / "story_library.json"
    story_constructor = tmp_path / "story_constructor.json"
    originals = {
        story_library: story_library.read_bytes(),
        story_constructor: story_constructor.read_bytes(),
    }
    real_atomic_write = local_admin_store._atomic_write

    def fail_second_write(path: Path, content: str) -> None:
        if path == story_constructor:
            raise OSError("synthetic write failure")
        real_atomic_write(path, content)

    monkeypatch.setattr(local_admin_store, "_atomic_write", fail_second_write)
    with pytest.raises(OSError, match="synthetic write failure"):
        local_admin_store.save_admin_files(
            [
                {"id": "story_library", "content": '{"meta":{"version":2}}'},
                {"id": "story_constructor", "content": '{"meta":{"version":2}}'},
            ]
        )

    assert story_library.read_bytes() == originals[story_library]
    assert story_constructor.read_bytes() == originals[story_constructor]
    assert len(list((tmp_path / ".admin-backups").glob("*.bak"))) == 2


def test_local_admin_save_fsyncs_files_and_directories(monkeypatch, tmp_path) -> None:
    _seed_admin_files(tmp_path)
    monkeypatch.setattr(local_admin_store, "DATA_ROOT", tmp_path)
    real_fsync = os.fsync
    fsynced_types: list[str] = []

    def record_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        fsynced_types.append("directory" if stat.S_ISDIR(mode) else "file")
        real_fsync(descriptor)

    monkeypatch.setattr(local_admin_store.os, "fsync", record_fsync)
    result = local_admin_store.save_admin_files(
        [{"id": "story_library", "content": '{"meta":{"version":2}}'}]
    )

    assert result["saved"] is True
    assert fsynced_types.count("file") >= 2
    assert fsynced_types.count("directory") >= 2


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
    assert captured["files"] == [{"id": "story_library", "content": '{"meta":{"version":2}}'}]
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
    assert "up -d --no-build --force-recreate backend bot" in remote_command
    assert " --build" not in remote_command


def test_publish_health_check_rejects_non_http_url(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.local_admin_publish.urllib.request.urlopen",
        lambda *_args, **_kwargs: pytest.fail("urlopen must not run"),
    )

    with pytest.raises(AdminPublishError) as error:
        _check_health(AdminPublishJob(id="job-health"), "file:///etc/passwd")

    assert error.value.code == "ADMIN_PUBLISH_HEALTH_URL_INVALID"


def test_logged_command_rejects_missing_stdout_pipe(monkeypatch, tmp_path) -> None:
    process = SimpleNamespace(
        stdout=None,
        kill=lambda: None,
        wait=lambda *, timeout: 0,
    )
    monkeypatch.setattr(
        "app.services.local_admin_publish.subprocess.Popen",
        lambda *args, **kwargs: process,
    )

    with pytest.raises(AdminPublishError, match="stdout") as error:
        _run_logged_command(
            AdminPublishJob(id="job-stdout"),
            ["git", "status"],
            cwd=tmp_path,
            timeout=10,
        )

    assert error.value.code == "ADMIN_PUBLISH_COMMAND_FAILED"


def test_local_admin_push_endpoints_are_removed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.local_admin.get_settings",
        lambda: SimpleNamespace(allow_dev_tma_auth=True),
    )

    client = TestClient(app)

    assert client.get("/api/admin/push/status").status_code == 404
    assert client.post("/api/admin/push/send", json={}).status_code == 404
    assert client.post("/api/admin/push/send-all", json={}).status_code == 404


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
