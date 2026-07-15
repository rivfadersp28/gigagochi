#!/bin/sh

set -eu

target_uid="${APP_UID:-10001}"
target_gid="${APP_GID:-10001}"

case "$target_uid" in
  '' | *[!0-9]*)
    echo "APP_UID and APP_GID must be numeric" >&2
    exit 2
    ;;
esac

case "$target_gid" in
  '' | *[!0-9]*)
    echo "APP_UID and APP_GID must be numeric" >&2
    exit 2
    ;;
esac

if [ "$(id -u)" -ne 0 ]; then
  echo "volume ownership migration must run as root" >&2
  exit 2
fi

mkdir -p /app/static/generated/.private/processing-tmp

for directory in /app/static/generated /app/logs /app/data/push; do
  if [ ! -d "$directory" ]; then
    echo "required volume mount is missing: $directory" >&2
    exit 1
  fi

  # Older images used the host's default 022 umask. Repair only entries that
  # still expose group/world bits; `-perm /077` keeps repeat runs cheap.
  find "$directory" -xdev -type f -perm /077 -exec chmod go-rwx {} +
  find "$directory" -xdev -type d -perm /077 -exec chmod go-rwx {} +

  # Existing deployments created these volumes as root. Only touch entries whose
  # ownership is actually stale, so repeat runs stay cheap and do not change ctime.
  find "$directory" -xdev \( ! -user "$target_uid" -o ! -group "$target_gid" \) \
    -exec chown -h "$target_uid:$target_gid" {} +
done
