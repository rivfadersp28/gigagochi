#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
# shellcheck source=volume-backup-common.sh
. "$SCRIPT_DIR/volume-backup-common.sh"

CONFIRMATION_TOKEN="REPLACE_PUSH_DATA_AND_GENERATED_ASSETS"

usage() {
    printf '%s\n' \
        "Usage: $0 --from BACKUP_DIR --confirm $CONFIRMATION_TOKEN [--rollback-root DIR]" >&2
}

backup_dir=
confirmation=
rollback_root=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --from)
            [ "$#" -ge 2 ] || die "--from requires a directory"
            backup_dir=$2
            shift 2
            ;;
        --confirm)
            [ "$#" -ge 2 ] || die "--confirm requires the exact confirmation token"
            confirmation=$2
            shift 2
            ;;
        --rollback-root)
            [ "$#" -ge 2 ] || die "--rollback-root requires a directory"
            rollback_root=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage
            die "unknown argument: $1"
            ;;
    esac
done

[ -n "$backup_dir" ] || die "--from is required"
[ "$confirmation" = "$CONFIRMATION_TOKEN" ] \
    || die "restore refused: pass the exact --confirm token shown in --help"
[ -d "$backup_dir" ] || die "backup directory not found: $backup_dir"
[ ! -L "$backup_dir" ] || die "backup directory must not be a symlink"
backup_dir=$(CDPATH= cd -P "$backup_dir" && pwd)

if [ -z "$rollback_root" ]; then
    rollback_root=$(dirname "$backup_dir")
fi
umask 077
mkdir -p "$rollback_root"
rollback_root=$(CDPATH= cd -P "$rollback_root" && pwd)

WRITERS_STOPPED=0
BACKEND_WAS_RUNNING=0
BOT_WAS_RUNNING=0
RESTORE_MUTATED=0
RESTORE_COMPLETE=0
ROLLBACK_DIR=
ROLLBACK_WORK_DIR=

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    set +e

    if [ -n "$ROLLBACK_WORK_DIR" ]; then
        case "$ROLLBACK_WORK_DIR" in
            "$rollback_root"/.gigagochi-pre-restore.incomplete.*)
                rm -rf -- "$ROLLBACK_WORK_DIR"
                ;;
        esac
    fi

    safe_to_restart=1
    if [ "$RESTORE_MUTATED" -eq 1 ] && [ "$RESTORE_COMPLETE" -eq 0 ]; then
        log "Restore failed after volume mutation; rolling back both volumes..."
        if [ -n "$ROLLBACK_DIR" ] \
            && replace_volumes_from_bundle "$ROLLBACK_DIR" \
            && repair_volume_permissions \
            && validate_volume_sqlite_databases; then
            log "Both volumes were rolled back to their pre-restore snapshot."
        else
            safe_to_restart=0
            log "CRITICAL: automatic rollback failed; backend and bot remain stopped."
        fi
    fi

    if [ "$WRITERS_STOPPED" -eq 1 ] && [ "$safe_to_restart" -eq 1 ]; then
        restart_original_writers || status=1
    fi
    exit "$status"
}

on_signal() {
    exit 130
}

trap cleanup EXIT
trap on_signal HUP INT TERM

validate_compose_environment

# Corrupt or incomplete bundles are rejected before production writers are stopped.
verify_bundle "$backup_dir"
validate_archive_members "$backup_dir"

capture_writer_state
stop_writers

# Verify again after stopping writers, immediately before any persistent data is changed.
verify_bundle "$backup_dir"
validate_archive_members "$backup_dir"

rollback_id="gigagochi-pre-restore-$(date -u '+%Y%m%dT%H%M%SZ')-$$"
ROLLBACK_DIR="$rollback_root/$rollback_id"
[ ! -e "$ROLLBACK_DIR" ] || die "rollback destination already exists: $ROLLBACK_DIR"
ROLLBACK_WORK_DIR=$(mktemp -d "$rollback_root/.gigagochi-pre-restore.incomplete.XXXXXX")
create_volume_bundle "$ROLLBACK_WORK_DIR" "$rollback_id" pre-restore
mv "$ROLLBACK_WORK_DIR" "$ROLLBACK_DIR"
sync -f "$rollback_root"
ROLLBACK_WORK_DIR=

RESTORE_MUTATED=1
replace_volumes_from_bundle "$backup_dir"
repair_volume_permissions
validate_volume_sqlite_databases
RESTORE_COMPLETE=1

log "Restore completed from: $backup_dir"
log "Pre-restore rollback retained at: $ROLLBACK_DIR"
