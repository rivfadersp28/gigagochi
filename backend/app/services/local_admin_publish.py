from __future__ import annotations

import selectors
import shlex
import subprocess
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.services.local_admin_store import (
    managed_admin_git_paths,
    save_admin_files,
    validate_admin_files_on_disk,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

PublishStatus = Literal["pending", "running", "succeeded", "failed"]
TERMINAL_STATUSES = {"succeeded", "failed"}


class AdminPublishError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class AdminPublishJob:
    id: str
    status: PublishStatus = "pending"
    created_at: str = field(default_factory=lambda: _now_iso())
    started_at: str | None = None
    finished_at: str | None = None
    logs: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    saved_files: list[dict[str, Any]] = field(default_factory=list)
    commit_sha: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def log(self, message: str, *, level: str = "info") -> None:
        with self._lock:
            self.logs.append(
                {
                    "at": _now_iso(),
                    "level": level,
                    "message": message,
                }
            )
            if len(self.logs) > 600:
                self.logs = self.logs[-600:]

    def set_status(self, status: PublishStatus) -> None:
        with self._lock:
            self.status = status
            if status == "running" and not self.started_at:
                self.started_at = _now_iso()
            if status in TERMINAL_STATUSES and not self.finished_at:
                self.finished_at = _now_iso()

    def fail(self, exc: AdminPublishError) -> None:
        with self._lock:
            self.status = "failed"
            self.error = exc.message
            self.error_code = exc.code
            self.finished_at = _now_iso()
        self.log(exc.message, level="error")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "createdAt": self.created_at,
                "startedAt": self.started_at,
                "finishedAt": self.finished_at,
                "logs": list(self.logs),
                "error": self.error,
                "errorCode": self.error_code,
                "savedFiles": list(self.saved_files),
                "commitSha": self.commit_sha,
            }


_jobs_lock = threading.Lock()
_jobs: dict[str, AdminPublishJob] = {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _setting(settings: Any, name: str, default: Any = None) -> Any:
    return getattr(settings, name, default)


def unexpected_publish_paths(paths: list[str] | tuple[str, ...]) -> list[str]:
    allowed = set(managed_admin_git_paths())
    return sorted(path for path in paths if path and path not in allowed)


def start_admin_publish(
    *,
    files: list[dict[str, str]],
    settings: Any,
    commit_message: str | None = None,
) -> dict[str, Any]:
    if not _setting(settings, "admin_publish_enabled", False):
        raise AdminPublishError(
            "ADMIN_PUBLISH_DISABLED",
            "Публикация отключена в backend env.",
        )
    if not _setting(settings, "admin_publish_ssh_target"):
        raise AdminPublishError(
            "ADMIN_PUBLISH_SSH_TARGET_MISSING",
            "Не задан ADMIN_PUBLISH_SSH_TARGET.",
        )

    with _jobs_lock:
        active = [job for job in _jobs.values() if job.status not in TERMINAL_STATUSES]
        if active:
            raise AdminPublishError(
                "ADMIN_PUBLISH_BUSY",
                "Публикация уже выполняется.",
            )
        job = AdminPublishJob(id=uuid.uuid4().hex)
        _jobs[job.id] = job

    thread = threading.Thread(
        target=_run_publish_job,
        args=(job, files, settings, commit_message),
        daemon=True,
    )
    thread.start()
    return job.snapshot()


def get_admin_publish_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
    return job.snapshot() if job else None


def _run_publish_job(
    job: AdminPublishJob,
    files: list[dict[str, str]],
    settings: Any,
    commit_message: str | None,
) -> None:
    job.set_status("running")
    try:
        _publish_admin_data(job, files, settings, commit_message)
    except AdminPublishError as exc:
        job.fail(exc)
        return
    except Exception as exc:  # pragma: no cover - defensive job boundary
        job.fail(AdminPublishError("ADMIN_PUBLISH_FAILED", str(exc)))
        return
    job.set_status("succeeded")
    job.log("Публикация завершена.")


def _publish_admin_data(
    job: AdminPublishJob,
    files: list[dict[str, str]],
    settings: Any,
    commit_message: str | None,
) -> None:
    if files:
        job.log("Сохраняю черновики перед публикацией.")
        save_result = save_admin_files(files)
        if not save_result["saved"]:
            raise AdminPublishError(
                "ADMIN_PUBLISH_SAVE_FAILED",
                f"Не удалось сохранить файлы: {save_result['errors']}",
            )
        job.saved_files = save_result["files"]
        job.log(
            "Сохранено локально: "
            + ", ".join(file["path"] for file in save_result["files"]),
        )

    job.log("Проверяю JSON/JSONL на диске.")
    validation_errors = validate_admin_files_on_disk()
    if validation_errors:
        raise AdminPublishError(
            "ADMIN_PUBLISH_VALIDATION_FAILED",
            f"Ошибки в data-файлах: {validation_errors}",
        )

    remote = str(_setting(settings, "admin_publish_git_remote", "origin"))
    branch = str(_setting(settings, "admin_publish_git_branch", "main"))
    timeout = float(_setting(settings, "admin_publish_command_timeout_seconds", 1200))
    allowed_paths = managed_admin_git_paths()

    job.log(f"Синхронизирую git refs: {remote}/{branch}.")
    _run_logged_command(job, ["git", "fetch", remote, branch], cwd=REPO_ROOT, timeout=timeout)

    behind_count = _behind_count(remote, branch, timeout)
    if behind_count:
        raise AdminPublishError(
            "ADMIN_PUBLISH_BRANCH_BEHIND",
            (
                f"Локальная ветка отстаёт от {remote}/{branch} "
                f"на {behind_count} commit."
            ),
        )

    unpublished_before = _unpublished_paths(job, remote, branch, timeout)
    unexpected_before = unexpected_publish_paths(unpublished_before)
    if unexpected_before:
        raise AdminPublishError(
            "ADMIN_PUBLISH_UNEXPECTED_COMMITS",
            (
                "Неопубликовано вне админки: "
                + ", ".join(unexpected_before)
            ),
        )

    changed_allowed = _changed_paths(allowed_paths, timeout)
    if changed_allowed:
        job.log("Готовлю commit data-файлов админки.")
        staged_unexpected = unexpected_publish_paths(_staged_paths(timeout))
        if staged_unexpected:
            raise AdminPublishError(
                "ADMIN_PUBLISH_UNEXPECTED_STAGED",
                (
                    "В index уже лежат файлы вне админки: "
                    + ", ".join(staged_unexpected)
                ),
            )
        _run_logged_command(
            job,
            ["git", "add", "--", *allowed_paths],
            cwd=REPO_ROOT,
            timeout=timeout,
        )
        staged_unexpected = unexpected_publish_paths(_staged_paths(timeout))
        if staged_unexpected:
            raise AdminPublishError(
                "ADMIN_PUBLISH_UNEXPECTED_STAGED",
                (
                    "После git add в index попали файлы вне админки: "
                    + ", ".join(staged_unexpected)
                ),
            )
        message = _commit_message(commit_message)
        _run_logged_command(
            job,
            ["git", "commit", "-m", message, "--", *allowed_paths],
            cwd=REPO_ROOT,
            timeout=timeout,
        )
        job.commit_sha = _run_capture(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            timeout=timeout,
        )
        job.log(f"Создан commit {job.commit_sha}.")
    else:
        job.log("Нет data-изменений; commit не нужен.")

    unpublished_after = _unpublished_paths(job, remote, branch, timeout)
    unexpected_after = unexpected_publish_paths(unpublished_after)
    if unexpected_after:
        raise AdminPublishError(
            "ADMIN_PUBLISH_UNEXPECTED_COMMITS",
            (
                "Перед push найдено вне админки: "
                + ", ".join(unexpected_after)
            ),
        )

    job.log(f"Отправляю изменения в GitHub: {remote} HEAD:{branch}.")
    _run_logged_command(
        job,
        ["git", "push", remote, f"HEAD:{branch}"],
        cwd=REPO_ROOT,
        timeout=timeout,
    )

    _deploy_on_hetzner(job, settings, timeout)
    _check_health(job, str(_setting(settings, "admin_publish_health_url", "")))


def _changed_paths(paths: tuple[str, ...], timeout: float) -> list[str]:
    output = _run_capture(
        ["git", "status", "--porcelain", "--untracked-files=no", "--", *paths],
        cwd=REPO_ROOT,
        timeout=timeout,
    )
    return [line[3:] for line in output.splitlines() if line.strip()]


def _staged_paths(timeout: float) -> list[str]:
    output = _run_capture(
        ["git", "diff", "--cached", "--name-only"],
        cwd=REPO_ROOT,
        timeout=timeout,
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def _behind_count(remote: str, branch: str, timeout: float) -> int:
    output = _run_capture(
        ["git", "rev-list", "--count", f"HEAD..{remote}/{branch}"],
        cwd=REPO_ROOT,
        timeout=timeout,
    )
    return int(output or "0")


def _unpublished_paths(
    job: AdminPublishJob,
    remote: str,
    branch: str,
    timeout: float,
) -> list[str]:
    output = _run_capture(
        ["git", "diff", "--name-only", f"{remote}/{branch}..HEAD"],
        cwd=REPO_ROOT,
        timeout=timeout,
    )
    paths = [line.strip() for line in output.splitlines() if line.strip()]
    if paths:
        job.log(f"Неопубликованные файлы: {', '.join(paths)}")
    return paths


def _deploy_on_hetzner(job: AdminPublishJob, settings: Any, timeout: float) -> None:
    ssh_target = str(_setting(settings, "admin_publish_ssh_target", ""))
    if not ssh_target or any(char.isspace() for char in ssh_target):
        raise AdminPublishError(
            "ADMIN_PUBLISH_SSH_TARGET_INVALID",
            "ADMIN_PUBLISH_SSH_TARGET должен быть SSH target без пробелов.",
        )
    remote_path = str(_setting(settings, "admin_publish_remote_path", "/opt/gigagochi"))
    ssh_args = ["ssh", "-o", "BatchMode=yes"]
    ssh_key = _setting(settings, "admin_publish_ssh_key_path")
    if ssh_key:
        ssh_args.extend(["-i", str(Path(str(ssh_key)).expanduser())])
    remote_command = (
        f"set -e; cd {shlex.quote(remote_path)}; "
        "git pull --ff-only; "
        "docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build; "
        "docker compose --env-file .env.production -f docker-compose.prod.yml ps"
    )
    job.log(f"Запускаю deploy на Hetzner: {ssh_target}.")
    _run_logged_command(
        job,
        [*ssh_args, ssh_target, remote_command],
        cwd=REPO_ROOT,
        timeout=timeout,
    )


def _check_health(job: AdminPublishJob, health_url: str) -> None:
    if not health_url:
        job.log(
            "Health-check URL не задан; пропускаю проверку.",
            level="warning",
        )
        return
    job.log(f"Проверяю health-check: {health_url}.")
    try:
        with urllib.request.urlopen(health_url, timeout=20) as response:
            status = response.status
            body = response.read(300).decode("utf-8", errors="replace")
    except Exception as exc:
        raise AdminPublishError("ADMIN_PUBLISH_HEALTH_FAILED", str(exc)) from exc
    if status < 200 or status >= 400:
        raise AdminPublishError(
            "ADMIN_PUBLISH_HEALTH_FAILED",
            f"Health-check вернул HTTP {status}: {body}",
        )
    job.log(f"Health-check OK: HTTP {status}.")


def _commit_message(message: str | None) -> str:
    text = (message or "").strip()
    if text:
        return text[:180]
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"Update admin data ({stamp})"


def _format_command(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def _run_capture(args: list[str], *, cwd: Path, timeout: float) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        message = (
            f"Команда завершилась с кодом {completed.returncode}: "
            f"{_format_command(args)}\n{output}"
        )
        raise AdminPublishError(
            "ADMIN_PUBLISH_COMMAND_FAILED",
            message,
        )
    return completed.stdout.strip()


def _run_logged_command(
    job: AdminPublishJob,
    args: list[str],
    *,
    cwd: Path,
    timeout: float,
) -> None:
    job.log(f"$ {_format_command(args)}")
    try:
        process = subprocess.Popen(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as exc:
        raise AdminPublishError(
            "ADMIN_PUBLISH_COMMAND_FAILED",
            f"Не удалось запустить команду: {_format_command(args)}",
        ) from exc

    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while True:
            for key, _ in selector.select(timeout=0.25):
                line = key.fileobj.readline()
                if line:
                    job.log(line.rstrip())
            if process.poll() is not None:
                break
            if time.monotonic() > deadline:
                process.kill()
                process.wait(timeout=5)
                raise AdminPublishError(
                    "ADMIN_PUBLISH_COMMAND_TIMEOUT",
                    (
                        f"Команда не завершилась за {timeout:.0f} сек: "
                        f"{_format_command(args)}"
                    ),
                )
        for line in process.stdout.read().splitlines():
            job.log(line.rstrip())
    finally:
        selector.unregister(process.stdout)
        process.stdout.close()

    if process.returncode != 0:
        message = (
            f"Команда завершилась с кодом {process.returncode}: "
            f"{_format_command(args)}"
        )
        raise AdminPublishError(
            "ADMIN_PUBLISH_COMMAND_FAILED",
            message,
        )
