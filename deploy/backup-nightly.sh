#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)

if [ "$(id -u)" -ne 0 ]; then
    echo "Nightly backup must run as root" >&2
    exit 1
fi

backup_root=${BACKUP_ROOT:-/var/backups/gigagochi}
offsite_remote=${BACKUP_OFFSITE_REMOTE:-}
retention_days=${BACKUP_LOCAL_RETENTION_DAYS:-7}

[ -n "$offsite_remote" ] || {
    echo "BACKUP_OFFSITE_REMOTE is required" >&2
    exit 1
}
case "$retention_days" in
    ''|*[!0-9]*)
        echo "BACKUP_LOCAL_RETENTION_DAYS must be a non-negative integer" >&2
        exit 1
        ;;
esac

command -v rclone >/dev/null 2>&1 || {
    echo "Command not found: rclone" >&2
    exit 1
}

umask 077
mkdir -p "$backup_root"
backup_root=$(CDPATH= cd -P "$backup_root" && pwd)
offsite_remote=${offsite_remote%/}

backup_dir=$("$SCRIPT_DIR/backup-volumes.sh" "$backup_root")
case "$backup_dir" in
    "$backup_root"/gigagochi-volumes-*) ;;
    *)
        echo "Unexpected backup path: $backup_dir" >&2
        exit 1
        ;;
esac

bundle_name=${backup_dir##*/}
remote_dir="$offsite_remote/$bundle_name"
rclone copy "$backup_dir" "$remote_dir" --immutable
rclone check "$backup_dir" "$remote_dir" --one-way
echo "Verified off-site backup: $remote_dir"

find "$backup_root" -mindepth 1 -maxdepth 1 -type d \
    -name 'gigagochi-volumes-*' -mtime "+$retention_days" -print |
while IFS= read -r candidate; do
    candidate_name=${candidate##*/}
    if rclone check "$candidate" "$offsite_remote/$candidate_name" --one-way >/dev/null; then
        rm -rf -- "$candidate"
        echo "Removed verified local backup: $candidate"
    else
        echo "Kept unverified local backup: $candidate" >&2
    fi
done
