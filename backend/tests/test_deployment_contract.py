import re
import stat
import subprocess
from pathlib import Path

import yaml

from app.config import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_docker_context_excludes_runtime_user_data() -> None:
    entries = {
        line.strip().rstrip("/")
        for line in (BACKEND_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "!"))
    }

    assert "data/push" in entries
    assert "data/.admin-backups" in entries


def test_compose_migrates_persistent_volume_ownership_before_backend() -> None:
    repository_root = BACKEND_ROOT.parent

    for compose_name in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose = yaml.safe_load((repository_root / compose_name).read_text(encoding="utf-8"))
        services = compose["services"]
        migration = services["volume-permissions"]

        assert migration["user"] == "0:0"
        assert migration["network_mode"] == "none"
        assert migration["read_only"] is True
        assert set(migration["cap_add"]) == {"CHOWN", "DAC_OVERRIDE", "FOWNER"}
        assert migration["command"] == [
            "sh",
            "/app/scripts/ensure_volume_permissions.sh",
        ]
        assert set(migration["volumes"]) == {
            "generated_assets:/app/static/generated",
            "backend_logs:/app/logs",
            "push_data:/app/data/push",
        }
        assert services["backend"]["depends_on"]["volume-permissions"] == {
            "condition": "service_completed_successfully"
        }


def test_volume_ownership_migration_is_scoped_and_idempotent() -> None:
    script = (BACKEND_ROOT / "scripts/ensure_volume_permissions.sh").read_text(encoding="utf-8")

    assert "APP_UID:-10001" in script
    assert "APP_GID:-10001" in script
    assert 'find "$directory" -xdev' in script
    assert '! -user "$target_uid"' in script
    assert '! -group "$target_gid"' in script
    assert "chown -h" in script
    assert "chown -R" not in script
    assert "mkdir -p /app/static/generated/.private/processing-tmp" in script
    assert 'find "$directory" -xdev -type f -perm /077 -exec chmod go-rwx {} +' in script
    assert 'find "$directory" -xdev -type d -perm /077 -exec chmod go-rwx {} +' in script


def test_backend_and_bot_share_storage_admission_configuration() -> None:
    repository_root = BACKEND_ROOT.parent
    compose = yaml.safe_load(
        (repository_root / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    backend_environment = services["backend"]["environment"]
    bot_environment = services["bot"]["environment"]
    shared_keys = {
        "STORAGE_HEALTH_MIN_FREE_BYTES",
        "STORAGE_HEALTH_MIN_FREE_PERCENT",
        "STORAGE_HEALTH_PROBE_CACHE_SECONDS",
        "STORAGE_ADMISSION_IMAGE_RESERVE_BYTES",
        "STORAGE_ADMISSION_VIDEO_RESERVE_BYTES",
        "MEDIA_IMAGE_CONCURRENCY",
        "MEDIA_VIDEO_CONCURRENCY",
    }

    assert {key: backend_environment[key] for key in shared_keys} == {
        key: bot_environment[key] for key in shared_keys
    }
    assert backend_environment["TMPDIR"] == "/app/static/generated/.private/processing-tmp"
    assert bot_environment["TMPDIR"] == backend_environment["TMPDIR"]


def test_prod_compose_does_not_override_backend_env_from_another_file() -> None:
    repository_root = BACKEND_ROOT.parent
    compose = yaml.safe_load(
        (repository_root / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    backend_owned_keys = {
        "GENERATED_MEDIA_CLEANUP_ENABLED",
        "TELEGRAM_DAILY_PUSH_ENABLED",
        "TELEGRAM_DAILY_PUSH_HOURS",
        "TELEGRAM_DAILY_PUSH_WINDOW_MINUTES",
        "TELEGRAM_DAILY_PUSH_DEFAULT_TIMEZONE",
        "BACKGROUND_STORY_ENABLED",
        "BACKGROUND_STORY_INTERVAL_SECONDS",
        "BACKGROUND_STORY_HOURS",
        "BACKGROUND_STORY_WINDOW_MINUTES",
        "LLM_PROFILE",
        "GIGACHAT_BASE_URL",
        "GIGACHAT_USERNAME",
        "GIGACHAT_PASSWORD",
        "GIGACHAT_MODEL",
        "GIGACHAT_SSL_VERIFY",
        "GIGACHAT_CA_BUNDLE",
        "GIGACHAT_TOKEN_TIMEOUT_SECONDS",
        "GIGACHAT_CHAT_TIMEOUT_SECONDS",
        "GIGACHAT_TOKEN_TTL_SECONDS",
        "OPENROUTER_VIDEO_MODEL",
        "OPENROUTER_VIDEO_TIMEOUT_SECONDS",
        "OPENROUTER_VIDEO_POLL_INTERVAL_SECONDS",
        "OPENAI_MAX_RETRIES",
        "OPS_ALERTS_ENABLED",
        "OPS_ALERT_TELEGRAM_IDS",
        "DIAGNOSTIC_TELEGRAM_IDS",
        "INTERACTIVE_TRAVEL_PILOT_TELEGRAM_IDS",
    }

    for service_name in ("backend", "bot"):
        service = services[service_name]
        assert service["env_file"] == ["./backend/.env"]
        assert backend_owned_keys.isdisjoint(service["environment"])


def test_frontend_build_defaults_to_same_origin_api() -> None:
    repository_root = BACKEND_ROOT.parent
    dockerfile = (repository_root / "frontend/Dockerfile").read_text(encoding="utf-8")
    api_transport = (repository_root / "frontend/src/lib/apiTransport.ts").read_text(
        encoding="utf-8"
    )
    development_compose = yaml.safe_load(
        (repository_root / "docker-compose.yml").read_text(encoding="utf-8")
    )

    assert re.search(r"^ARG NEXT_PUBLIC_API_URL=$", dockerfile, re.MULTILINE)
    assert 'process.env.NEXT_PUBLIC_API_URL?.trim() ?? ""' in api_transport
    assert "127.0.0.1:8000" not in api_transport
    assert (
        development_compose["services"]["frontend"]["build"]["args"]["NEXT_PUBLIC_API_URL"]
        == "${NEXT_PUBLIC_API_URL:-http://localhost:8000}"
    )


def test_frontend_containers_are_non_writable_and_drop_linux_capabilities() -> None:
    repository_root = BACKEND_ROOT.parent

    for compose_name in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose = yaml.safe_load((repository_root / compose_name).read_text(encoding="utf-8"))
        frontend = compose["services"]["frontend"]

        assert frontend["read_only"] is True
        assert frontend["cap_drop"] == ["ALL"]
        assert frontend["security_opt"] == ["no-new-privileges:true"]
        assert "/tmp:size=64m,mode=1777" in frontend["tmpfs"]
        assert "/app/.next/cache:size=128m,mode=0700,uid=1001,gid=1001" in frontend["tmpfs"]


def test_backend_shutdown_grace_period_can_drain_paid_jobs() -> None:
    repository_root = BACKEND_ROOT.parent

    for compose_name in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose = yaml.safe_load((repository_root / compose_name).read_text(encoding="utf-8"))
        assert compose["services"]["backend"]["stop_grace_period"] == "20m"


def test_generation_backend_is_one_uvicorn_process_without_replica_scaling() -> None:
    repository_root = BACKEND_ROOT.parent
    dockerfile = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert (
        'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]'
    ) in dockerfile
    for compose_name in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose = yaml.safe_load((repository_root / compose_name).read_text(encoding="utf-8"))
        backend = compose["services"]["backend"]

        assert backend.get("command") is None
        assert backend.get("deploy", {}).get("replicas", 1) == 1


def test_prod_compose_interpolation_uses_declared_compose_env() -> None:
    repository_root = BACKEND_ROOT.parent
    compose_text = (repository_root / "docker-compose.prod.yml").read_text(encoding="utf-8")
    compose_env_keys = {
        line.split("=", maxsplit=1)[0].strip()
        for line in (repository_root / "deploy/compose.env.production.example")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    interpolated_keys = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", compose_text))

    assert interpolated_keys == compose_env_keys
    assert interpolated_keys.isdisjoint(
        _env_example_keys(repository_root / "deploy/backend.env.production.example")
    )


def _env_example_keys(path: Path) -> set[str]:
    return {
        line.split("=", maxsplit=1)[0].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }


def test_backend_production_env_contains_only_supported_settings() -> None:
    repository_root = BACKEND_ROOT.parent
    production_keys = _env_example_keys(repository_root / "deploy/backend.env.production.example")
    supported_keys = {field_name.upper() for field_name in Settings.model_fields}
    supported_keys.add("AI_PROMPT_LOG_FULL")

    assert production_keys <= supported_keys


def test_push_registry_defaults_and_env_examples_use_sqlite_with_legacy_import() -> None:
    repository_root = BACKEND_ROOT.parent
    settings = Settings(_env_file=None)

    assert settings.telegram_push_store_path.endswith("telegram_push_state.sqlite3")
    assert settings.telegram_push_store_backend == "auto"
    assert settings.telegram_push_legacy_json_path is not None
    assert settings.telegram_push_legacy_json_path.endswith("telegram_push_state.json")
    assert settings.telegram_push_legacy_json_required is True

    for env_path in (
        BACKEND_ROOT / ".env.example",
        repository_root / "deploy/backend.env.production.example",
    ):
        values = {
            line.split("=", maxsplit=1)[0]: line.split("=", maxsplit=1)[1]
            for line in env_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#") and "=" in line
        }
        assert values["TELEGRAM_PUSH_STORE_PATH"].endswith("telegram_push_state.sqlite3")
        assert values["TELEGRAM_PUSH_STORE_BACKEND"] == "auto"
        assert values["TELEGRAM_PUSH_LEGACY_JSON_PATH"].endswith("telegram_push_state.json")
        assert values["TELEGRAM_PUSH_LEGACY_JSON_REQUIRED"] == "true"

    deploy_guide = (repository_root / "deploy/HETZNER.md").read_text(encoding="utf-8")
    assert "stop backend bot" in deploy_guide
    assert "legacy-json-v1" in deploy_guide
    assert "PRAGMA quick_check" in deploy_guide
    assert "ok imported <expected-count> True" in deploy_guide


def test_async_provider_receipt_store_uses_persistent_push_volume() -> None:
    repository_root = BACKEND_ROOT.parent

    assert Settings.model_fields["provider_task_receipt_store_path"].default == (
        "data/push/provider_task_receipts.sqlite3"
    )
    assert Settings.model_fields["provider_task_receipt_store_max_records"].default == 100_000
    for env_path in (
        BACKEND_ROOT / ".env.example",
        repository_root / "deploy/backend.env.production.example",
    ):
        values = {
            line.split("=", maxsplit=1)[0]: line.split("=", maxsplit=1)[1]
            for line in env_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#") and "=" in line
        }
        assert values["PROVIDER_TASK_RECEIPT_STORE_PATH"].endswith(
            "/push/provider_task_receipts.sqlite3"
        )
        assert values["PROVIDER_TASK_RECEIPT_STORE_MAX_RECORDS"] == "100000"


def test_backend_production_env_exposes_paid_kill_switches_and_recovery_config() -> None:
    repository_root = BACKEND_ROOT.parent
    production_keys = _env_example_keys(repository_root / "deploy/backend.env.production.example")
    required_keys = {
        "BACKGROUND_STORY_ENABLED",
        "BACKGROUND_STORY_INTERVAL_SECONDS",
        "BACKGROUND_STORY_HOURS",
        "BACKGROUND_STORY_WINDOW_MINUTES",
        "DIAGNOSTIC_TELEGRAM_IDS",
        "GENERATED_MEDIA_CLEANUP_ENABLED",
        "LLM_PROFILE",
        "OPENROUTER_VIDEO_MODEL",
        "OPENROUTER_VIDEO_TIMEOUT_SECONDS",
        "OPENROUTER_VIDEO_POLL_INTERVAL_SECONDS",
        "OPENAI_MAX_RETRIES",
    }

    assert required_keys <= production_keys


def test_recommended_host_caddy_keeps_production_security_headers() -> None:
    repository_root = BACKEND_ROOT.parent
    host_caddy = (repository_root / "deploy/Caddyfile.host.example").read_text(encoding="utf-8")
    required_directives = {
        '>Strict-Transport-Security "max-age=31536000; includeSubDomains"',
        '>X-Content-Type-Options "nosniff"',
        '>Referrer-Policy "strict-origin-when-cross-origin"',
        '>Permissions-Policy "camera=(), microphone=(), geolocation=()"',
        "-X-Powered-By",
    }

    for directive in required_directives:
        assert directive in host_caddy


def test_prod_compose_uses_only_the_existing_external_proxy() -> None:
    repository_root = BACKEND_ROOT.parent
    compose = yaml.safe_load(
        (repository_root / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )

    assert "caddy" not in compose["services"]
    assert compose["networks"]["public_proxy"]["external"] is True
    for service_name in ("backend", "frontend"):
        assert "expose" not in compose["services"][service_name]


def test_volume_backup_and_restore_scripts_have_safe_static_contract() -> None:
    repository_root = BACKEND_ROOT.parent
    deploy_root = repository_root / "deploy"
    common_path = deploy_root / "volume-backup-common.sh"
    backup_path = deploy_root / "backup-volumes.sh"
    restore_path = deploy_root / "restore-volumes.sh"

    for script_path in (common_path, backup_path, restore_path):
        subprocess.run(["sh", "-n", str(script_path)], check=True)

    for executable_path in (backup_path, restore_path):
        assert executable_path.stat().st_mode & stat.S_IXUSR

    common = common_path.read_text(encoding="utf-8")
    backup = backup_path.read_text(encoding="utf-8")
    restore = restore_path.read_text(encoding="utf-8")

    assert 'BACKUP_FORMAT="gigagochi-volume-backup-v1"' in common
    assert 'GENERATED_ARCHIVE="generated_assets.tar.gz"' in common
    assert 'PUSH_ARCHIVE="push_data.tar.gz"' in common
    assert "sha256sum --check --strict" in common
    assert 'die "run this production volume operation as root"' in common
    assert "validate_archive_members" in common
    assert 'sync -f "$bundle"' in common
    assert 'type != "-" && type != "d"' in common
    assert "compose stop backend bot" in common
    assert "tar -C /app/static/generated -czf /backup/generated_assets.tar.gz ." in common
    assert "tar -C /app/data/push -czf /backup/push_data.tar.gz ." in common
    assert "sync -f /app/static/generated" in common
    assert "sync -f /app/data/push" in common
    assert 'connection.execute("PRAGMA quick_check")' in common
    assert "--user 10001:10001" in common

    assert "trap cleanup EXIT" in backup
    assert "capture_writer_state" in backup
    assert backup.index("stop_writers") < backup.index("create_volume_bundle")
    assert backup.index("stop_writers") < backup.index("validate_volume_sqlite_databases")
    assert backup.index("validate_volume_sqlite_databases") < backup.index("create_volume_bundle")
    assert "mktemp -d" in backup
    assert 'sync -f "$backup_root"' in backup
    assert "purpose=$3" in common
    stop_writers_body = common.split("stop_writers() {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    assert stop_writers_body.index("WRITERS_STOPPED=1") < stop_writers_body.index(
        "compose stop backend bot"
    )

    assert 'CONFIRMATION_TOKEN="REPLACE_PUSH_DATA_AND_GENERATED_ASSETS"' in restore
    assert "trap cleanup EXIT" in restore
    assert restore.count('verify_bundle "$backup_dir"') >= 2
    assert restore.index('verify_bundle "$backup_dir"') < restore.index("stop_writers")
    assert restore.index("stop_writers") < restore.index(
        'create_volume_bundle "$ROLLBACK_WORK_DIR"'
    )
    assert restore.index('create_volume_bundle "$ROLLBACK_WORK_DIR"') < restore.index(
        'replace_volumes_from_bundle "$backup_dir"'
    )
    assert 'replace_volumes_from_bundle "$ROLLBACK_DIR"' in restore
    assert restore.count("validate_volume_sqlite_databases") >= 2
    assert "safe_to_restart=0" in restore
    assert ".gigagochi-pre-restore.incomplete." in restore
    assert 'sync -f "$rollback_root"' in restore


def test_volume_backup_helper_uses_exact_prod_named_volume_mounts() -> None:
    repository_root = BACKEND_ROOT.parent
    compose = yaml.safe_load(
        (repository_root / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    helper_volumes = set(compose["services"]["volume-permissions"]["volumes"])

    assert {
        "generated_assets:/app/static/generated",
        "push_data:/app/data/push",
    } <= helper_volumes
