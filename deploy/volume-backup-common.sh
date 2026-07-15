#!/bin/sh

# Shared implementation for backup-volumes.sh and restore-volumes.sh.
# The callers must stop backend and bot before calling create_volume_bundle or
# replace_volumes_from_bundle.

BACKUP_FORMAT="gigagochi-volume-backup-v1"
GENERATED_ARCHIVE="generated_assets.tar.gz"
PUSH_ARCHIVE="push_data.tar.gz"
MANIFEST_FILE="manifest.txt"
CHECKSUM_FILE="SHA256SUMS"

SCRIPT_DIR=${SCRIPT_DIR:-$(CDPATH= cd -P "$(dirname "$0")" && pwd)}
REPOSITORY_ROOT=${REPOSITORY_ROOT:-$(CDPATH= cd -P "$SCRIPT_DIR/.." && pwd)}
COMPOSE_FILE=${GIGAGOCHI_COMPOSE_FILE:-$REPOSITORY_ROOT/docker-compose.prod.yml}
COMPOSE_ENV_FILE=${GIGAGOCHI_COMPOSE_ENV_FILE:-$REPOSITORY_ROOT/.env.production}

log() {
    printf '%s\n' "$*" >&2
}

die() {
    log "ERROR: $*"
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command is missing: $1"
}

compose() {
    docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

validate_compose_environment() {
    require_command id
    require_command docker
    require_command sha256sum
    require_command awk
    require_command grep
    require_command mktemp
    require_command sync
    [ "$(id -u)" -eq 0 ] || die "run this production volume operation as root"
    [ -f "$COMPOSE_FILE" ] || die "compose file not found: $COMPOSE_FILE"
    [ -f "$COMPOSE_ENV_FILE" ] || die "compose env file not found: $COMPOSE_ENV_FILE"
    compose config --quiet
}

capture_writer_state() {
    BACKEND_WAS_RUNNING=0
    BOT_WAS_RUNNING=0
    if [ -n "$(compose ps --status running --quiet backend)" ]; then
        BACKEND_WAS_RUNNING=1
    fi
    if [ -n "$(compose ps --status running --quiet bot)" ]; then
        BOT_WAS_RUNNING=1
    fi
}

stop_writers() {
    log "Stopping backend and bot for a consistent volume snapshot..."
    # Set the cleanup obligation before Compose can partially stop one service
    # and then fail or receive a signal. Re-running `up` for an originally
    # running service is safe even when `stop` failed before changing it.
    WRITERS_STOPPED=1
    compose stop backend bot
}

restart_original_writers() {
    restart_status=0

    if [ "${BACKEND_WAS_RUNNING:-0}" -eq 1 ]; then
        compose up -d --no-deps backend || restart_status=1
    fi
    if [ "${BOT_WAS_RUNNING:-0}" -eq 1 ]; then
        compose up -d --no-deps bot || restart_status=1
    fi

    if [ "$restart_status" -ne 0 ]; then
        log "ERROR: failed to restore the original backend/bot running state"
    fi
    return "$restart_status"
}

assert_existing_stack_for_backup() {
    backend_container=$(compose ps --all --quiet backend)
    bot_container=$(compose ps --all --quiet bot)
    if [ -z "$backend_container" ] && [ -z "$bot_container" ]; then
        die "backend/bot containers do not exist; refusing to create a possibly empty backup"
    fi
}

validate_volume_sqlite_databases() {
    compose run --rm --no-deps -T --user 10001:10001 \
        volume-permissions \
        python -c '
import sqlite3
from pathlib import Path

roots = (Path("/app/static/generated"), Path("/app/data/push"))
for root in roots:
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            continue
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            result = connection.execute("PRAGMA quick_check").fetchall()
        finally:
            connection.close()
        if result != [("ok",)]:
            raise SystemExit(f"SQLite quick_check failed for {path}: {result}")
'
}

archive_volume_data() {
    destination=$1
    compose run --rm --no-deps -T --user 0:0 \
        --volume "$destination:/backup" \
        volume-permissions \
        sh -eu -c '
            umask 077
            test -d /app/static/generated
            test -d /app/data/push
            tar -C /app/static/generated -czf /backup/generated_assets.tar.gz .
            tar -C /app/data/push -czf /backup/push_data.tar.gz .
        '
}

validate_archive_members() {
    bundle=$1
    compose run --rm --no-deps -T --user 0:0 \
        --volume "$bundle:/backup:ro" \
        volume-permissions \
        sh -eu -c '
            validate_archive() {
                archive=$1
                tar -tzf "$archive" |
                    while IFS= read -r entry; do
                        case "$entry" in
                            .|./|./*) ;;
                            *)
                                echo "unsafe archive member path: $entry" >&2
                                exit 41
                                ;;
                        esac
                        case "$entry" in
                            ..|../*|*/..|*/../*)
                                echo "archive traversal member rejected: $entry" >&2
                                exit 42
                                ;;
                        esac
                    done
                tar -tvzf "$archive" |
                    awk '\''
                        BEGIN { count = 0 }
                        {
                            type = substr($0, 1, 1)
                            if (type != "-" && type != "d") {
                                exit 43
                            }
                            count += 1
                        }
                        END { if (count == 0) exit 44 }
                    '\''
            }

            validate_archive /backup/generated_assets.tar.gz
            validate_archive /backup/push_data.tar.gz
        '
}

write_bundle_metadata() {
    bundle=$1
    backup_id=$2
    purpose=$3
    created_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

    {
        printf 'format=%s\n' "$BACKUP_FORMAT"
        printf 'backup_id=%s\n' "$backup_id"
        printf 'created_at_utc=%s\n' "$created_at"
        printf 'purpose=%s\n' "$purpose"
        printf 'volumes=generated_assets push_data\n'
        printf 'writers=backend bot\n'
    } >"$bundle/$MANIFEST_FILE"

    (
        CDPATH= cd -P "$bundle"
        sha256sum "$GENERATED_ARCHIVE" "$PUSH_ARCHIVE" "$MANIFEST_FILE" >"$CHECKSUM_FILE"
    )
    chmod 0400 \
        "$bundle/$GENERATED_ARCHIVE" \
        "$bundle/$PUSH_ARCHIVE" \
        "$bundle/$MANIFEST_FILE" \
        "$bundle/$CHECKSUM_FILE"
}

verify_bundle() {
    bundle=$1

    for file in "$GENERATED_ARCHIVE" "$PUSH_ARCHIVE" "$MANIFEST_FILE" "$CHECKSUM_FILE"; do
        [ -f "$bundle/$file" ] || die "backup file is missing: $bundle/$file"
        [ ! -L "$bundle/$file" ] || die "backup file must not be a symlink: $bundle/$file"
    done

    checksum_lines=$(wc -l <"$bundle/$CHECKSUM_FILE" | awk '{print $1}')
    [ "$checksum_lines" -eq 3 ] || die "checksum file must contain exactly three entries"
    for file in "$GENERATED_ARCHIVE" "$PUSH_ARCHIVE" "$MANIFEST_FILE"; do
        matches=$(grep -Ec "^[0-9a-f]{64}  ${file}$" "$bundle/$CHECKSUM_FILE" || true)
        [ "$matches" -eq 1 ] || die "invalid checksum entry for $file"
    done

    manifest_lines=$(wc -l <"$bundle/$MANIFEST_FILE" | awk '{print $1}')
    [ "$manifest_lines" -eq 6 ] || die "manifest must contain exactly six fields"
    [ "$(grep -Fxc "format=$BACKUP_FORMAT" "$bundle/$MANIFEST_FILE" || true)" -eq 1 ] \
        || die "unsupported backup format"
    [ "$(grep -Exc 'backup_id=[A-Za-z0-9._-]+' "$bundle/$MANIFEST_FILE" || true)" -eq 1 ] \
        || die "invalid backup id"
    [ "$(grep -Exc 'created_at_utc=[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z' "$bundle/$MANIFEST_FILE" || true)" -eq 1 ] \
        || die "invalid backup timestamp"
    [ "$(grep -Exc 'purpose=(snapshot|pre-restore)' "$bundle/$MANIFEST_FILE" || true)" -eq 1 ] \
        || die "invalid backup purpose"
    [ "$(grep -Fxc 'volumes=generated_assets push_data' "$bundle/$MANIFEST_FILE" || true)" -eq 1 ] \
        || die "manifest does not describe both required volumes"
    [ "$(grep -Fxc 'writers=backend bot' "$bundle/$MANIFEST_FILE" || true)" -eq 1 ] \
        || die "manifest does not describe both stopped writers"

    (
        CDPATH= cd -P "$bundle"
        sha256sum --check --strict "$CHECKSUM_FILE"
    )
}

create_volume_bundle() {
    bundle=$1
    backup_id=$2
    purpose=$3

    archive_volume_data "$bundle"
    write_bundle_metadata "$bundle" "$backup_id" "$purpose"
    verify_bundle "$bundle"
    validate_archive_members "$bundle"
    # Do not report a completed backup while its archive and metadata are only
    # in the kernel page cache. `sync -f` is available on the Linux deployment host.
    sync -f "$bundle"
}

replace_volumes_from_bundle() {
    bundle=$1
    compose run --rm --no-deps -T --user 0:0 \
        --volume "$bundle:/backup:ro" \
        volume-permissions \
        sh -eu -c '
            clear_volume() {
                directory=$1
                rm -rf -- "$directory"/* "$directory"/.[!.]* "$directory"/..?*
            }

            clear_volume /app/static/generated
            clear_volume /app/data/push
            tar -C /app/static/generated --numeric-owner --same-owner -xzf /backup/generated_assets.tar.gz
            tar -C /app/data/push --numeric-owner --same-owner -xzf /backup/push_data.tar.gz
            sync -f /app/static/generated
            sync -f /app/data/push
        '
}

repair_volume_permissions() {
    compose run --rm --no-deps -T volume-permissions
}
