#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
# shellcheck source=volume-backup-common.sh
. "$SCRIPT_DIR/volume-backup-common.sh"

usage() {
    printf 'Usage: %s [BACKUP_ROOT]\n' "$0" >&2
}

if [ "$#" -gt 1 ]; then
    usage
    exit 2
fi

backup_root=${1:-/var/backups/gigagochi}
umask 077
mkdir -p "$backup_root"
backup_root=$(CDPATH= cd -P "$backup_root" && pwd)

WRITERS_STOPPED=0
BACKEND_WAS_RUNNING=0
BOT_WAS_RUNNING=0
WORK_DIR=
BACKUP_PUBLISHED=0

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    set +e

    if [ "$BACKUP_PUBLISHED" -eq 0 ] && [ -n "$WORK_DIR" ]; then
        case "$WORK_DIR" in
            "$backup_root"/.gigagochi-backup.incomplete.*)
                rm -rf -- "$WORK_DIR"
                ;;
        esac
    fi
    if [ "$WRITERS_STOPPED" -eq 1 ]; then
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
assert_existing_stack_for_backup
capture_writer_state
stop_writers
repair_volume_permissions
validate_volume_sqlite_databases

backup_id="gigagochi-volumes-$(date -u '+%Y%m%dT%H%M%SZ')-$$"
final_dir="$backup_root/$backup_id"
[ ! -e "$final_dir" ] || die "backup destination already exists: $final_dir"
WORK_DIR=$(mktemp -d "$backup_root/.gigagochi-backup.incomplete.XXXXXX")

create_volume_bundle "$WORK_DIR" "$backup_id" snapshot
mv "$WORK_DIR" "$final_dir"
sync -f "$backup_root"
WORK_DIR=
BACKUP_PUBLISHED=1

log "Consistent backup created: $final_dir"
printf '%s\n' "$final_dir"
