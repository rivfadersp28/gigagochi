# Hetzner production deploy

Production host:

- IPv4: `167.233.103.46`
- Domain: `gigagochi.serega.works`
- IPv6 prefix: `2a01:4f8:c015:8b05::/64`

`gigagochi.serega.works` must point to the server before Caddy can issue TLS certificates.
Use an `A` record for `167.233.103.46`. If you want IPv6 too, add an `AAAA` record for the
server's concrete IPv6 address, not the `/64` prefix.

Current observation from outside: `https://gigagochi.serega.works/health` responds with the
backend health check through the existing host Caddy container.

The existing public Docker network is `bizzy-radio_default`. The app compose joins backend/frontend
to that network with these aliases:

- `gigagochi-backend`
- `gigagochi-frontend`

## First server setup

Run on the server as root:

```bash
apt-get update
apt-get install -y ca-certificates curl git ufw
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```

Clone the repository:

```bash
mkdir -p /opt/gigagochi
git clone https://github.com/rivfadersp28/gigagochi.git /opt/gigagochi
cd /opt/gigagochi
```

Create production env files:

```bash
cp deploy/compose.env.production.example .env.production
cp deploy/backend.env.production.example backend/.env
chmod 600 .env.production backend/.env
```

Fill `backend/.env` with real secrets:

- `BOT_TOKEN`
- `OPENAI_API_KEY` — всегда нужен для primary pet assets независимо от `AI_PROVIDER`/`MEDIA_PROFILE`
- `OPENROUTER_API_KEY` — нужен default `legacy` media profile для video
- `KANDINSKY_API_KEY` — только если включён Kandinsky route или `PET_COMPARISON_ENABLED=true`

For a confirmed clean install that has no restored `push_data` volume and no
`telegram_push_state.json`, set `TELEGRAM_PUSH_LEGACY_JSON_REQUIRED=false` before the first start.
Keep it `true` for every upgrade or restore where a legacy registry is expected: a missing file
then fails closed instead of committing an empty migration marker.

Create or verify the external proxy network before the first `up` (including a DR host):

```bash
PUBLIC_PROXY_NETWORK="$(sed -n 's/^PUBLIC_PROXY_NETWORK=//p' .env.production)"
test -n "$PUBLIC_PROXY_NETWORK"
docker network inspect "$PUBLIC_PROXY_NETWORK" >/dev/null 2>&1 \
  || docker network create "$PUBLIC_PROXY_NETWORK"
docker compose --env-file .env.production -f docker-compose.prod.yml config --quiet
```

## Deploy app containers

```bash
cd /opt/gigagochi
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

By default, this starts only the app containers. Backend and frontend bind to server loopback:

- `127.0.0.1:18080` for backend
- `127.0.0.1:13000` for frontend

## Existing Caddy container

Because this server already runs `bizzy-radio-caddy-1` on ports `80` and `443`, prefer adding this
site block to the existing `/opt/bizzy-radio/Caddyfile` instead of starting another Caddy container:

```bash
cat deploy/Caddyfile.host.example
```

If the existing Caddyfile has a global `{ auto_https off }` block, remove it before adding the
Gigagochi HTTPS site. The radio block can stay HTTP-only as `{$CADDY_SITE_ADDRESS}` / `:80`.

Then validate and reload the existing Caddy container:

```bash
docker exec bizzy-radio-caddy-1 caddy validate --config /etc/caddy/Caddyfile
docker exec bizzy-radio-caddy-1 caddy reload --config /etc/caddy/Caddyfile
```

The active MVP has no PostgreSQL service. Pet progress and chat history stay in Telegram WebView
`localStorage`; Telegram delivery state uses the persistent `push_data` volume.

## Persistent volume backup and restore

Back up `push_data` and `generated_assets` as one unit. The first volume contains Telegram
delivery/idempotency state; the second contains media addressed by URLs stored in that state.
Restoring only one of them can replay paid commands or leave saved media URLs broken.

Create a private backup directory and run a consistent snapshot:

```bash
install -d -m 0700 /var/backups/gigagochi
cd /opt/gigagochi
./deploy/backup-volumes.sh /var/backups/gigagochi
```

Run both volume scripts as root: the production env is root-readable and helper archives are
root-owned by design.

The script validates Compose without printing secrets, records whether `backend` and `bot` were
running, stops both writers, and restarts only the services that were running before the snapshot.
It atomically publishes a directory containing `generated_assets.tar.gz`, `push_data.tar.gz`,
`manifest.txt`, and `SHA256SUMS`. An incomplete archive is never published. Copy the whole backup
directory to independent storage; do not copy only one archive.

For nightly off-site backups, install and configure `rclone`, then install the included units:

```bash
apt-get update && apt-get install -y rclone
rclone config
install -m 0600 deploy/backup.env.example /etc/gigagochi-backup.env
install -m 0755 deploy/backup-nightly.sh /opt/gigagochi/deploy/backup-nightly.sh
install -m 0644 deploy/gigagochi-backup.service deploy/gigagochi-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now gigagochi-backup.timer
systemctl start gigagochi-backup.service
journalctl -u gigagochi-backup.service --no-pager -n 100
```

Edit `/etc/gigagochi-backup.env` before the first run so `BACKUP_OFFSITE_REMOTE` points to storage
outside this host. The job verifies every upload with `rclone check`. A local bundle is deleted by
retention only after that exact bundle has also passed an off-site check; remote retention stays
provider-controlled. Check the schedule with `systemctl list-timers gigagochi-backup.timer`.

Restore requires the production env file and the backend image because an isolated, networkless
Compose helper mounts the named volumes. On a new disaster-recovery host, prepare them first:

```bash
cd /opt/gigagochi
docker compose --env-file .env.production -f docker-compose.prod.yml build volume-permissions
```

Then restore the complete backup with the exact destructive confirmation token:

```bash
./deploy/restore-volumes.sh \
  --from /var/backups/gigagochi/gigagochi-volumes-YYYYMMDDTHHMMSSZ-PID \
  --confirm REPLACE_PUSH_DATA_AND_GENERATED_ASSETS
```

Before changing either volume, the restore verifies the strict checksum set, manifest, archive
paths, and archive entry types twice. It stops both writers and saves a second `pre-restore`
rollback bundle beside the source backup. If extraction fails after mutation, both volumes are
automatically returned to that pre-restore snapshot before writers restart. If rollback itself
fails, the script deliberately leaves `backend` and `bot` stopped. Never start them manually in
that state until both volumes have been recovered together. The pre-restore bundle is retained for
a manual rollback; remove it only after health, Telegram delivery, and several existing media URLs
have been verified. Its default location is next to the source backup; pass
`--rollback-root /another/private/path` when that filesystem is read-only or lacks room for the
current contents of both volumes.

## Update

### One-time Telegram push registry migration

The production registry now uses SQLite WAL. Before the first update from
`telegram_push_state.json`, create the normal two-volume backup, then add these settings without
removing the legacy file:

```env
TELEGRAM_PUSH_STORE_PATH=/app/data/push/telegram_push_state.sqlite3
TELEGRAM_PUSH_STORE_BACKEND=auto
TELEGRAM_PUSH_LEGACY_JSON_PATH=/app/data/push/telegram_push_state.json
TELEGRAM_PUSH_LEGACY_JSON_REQUIRED=true
```

Stop `backend` and `bot` together before deploying so no old process can append to JSON after the
new processes import it. The first SQLite opener takes the existing JSON file lock, imports every
record and writes a SHA-256 migration marker in the same `synchronous=FULL` transaction. The second
process sees that marker and never reimports or overwrites newer SQLite rows. If the required source
is missing, initialization rolls back and retries after the mount/file is restored.

```bash
cd /opt/gigagochi
./deploy/backup-volumes.sh /var/backups/gigagochi
docker compose --env-file .env.production -f docker-compose.prod.yml stop backend bot
git pull --ff-only origin main
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
docker compose --env-file .env.production -f docker-compose.prod.yml exec -T backend \
  python - <<'PY'
import hashlib
import sqlite3
from pathlib import Path

from app.services.telegram_push_service import _push_store

_push_store()
path = "/app/data/push/telegram_push_state.sqlite3"
source = Path("/app/data/push/telegram_push_state.json")
source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
with sqlite3.connect(path) as connection:
    integrity = connection.execute("PRAGMA quick_check").fetchone()
    marker = connection.execute(
        """
        SELECT status, source_sha256, imported_records
        FROM push_store_migrations
        WHERE name = 'legacy-json-v1'
        """
    ).fetchone()
print(
    integrity[0] if integrity else None,
    marker[0] if marker else None,
    marker[2] if marker else None,
    bool(marker and marker[1] == source_sha256),
)
PY
```

The verification must print `ok imported <expected-count> True`. Keep the old JSON and the
pre-deploy backup until Telegram delivery and snapshots have been checked. Do not switch one
process back to JSON after SQLite has accepted writes: that creates split-brain state; restore the
consistent pre-deploy volume backup instead.

Before the first update to the centralized application config, merge these keys into the existing
`backend/.env` without replacing its secrets:

```env
BACKGROUND_STORY_ENABLED=true
BACKGROUND_STORY_INTERVAL_SECONDS=300
BACKGROUND_STORY_HOURS=[9,13,17,21]
BACKGROUND_STORY_WINDOW_MINUTES=120
SCHEDULED_BACKGROUND_STORY_PAID_MEDIA_DAILY_CAP=16
DIAGNOSTIC_TELEGRAM_IDS=[62943754]
OPENROUTER_VIDEO_MODEL=bytedance/seedance-2.0
OPENROUTER_VIDEO_TIMEOUT_SECONDS=900
OPENROUTER_VIDEO_POLL_INTERVAL_SECONDS=5
```

Before restarting an upgraded installation, explicitly choose and add
`SCHEDULED_BACKGROUND_STORY_PAID_MEDIA_DAILY_CAP`; when the key is absent its intentional
fail-closed default is `0`, so scheduled stories become text-only.

The cap is one global fixed UTC 24-hour window shared through `RATE_LIMIT_STORE_PATH`; every
scheduled image or video provider submission attempt consumes one unit. Set it to `0` to fail
closed and deliver text-only stories (or recovered media) without paid media calls. A cap of `N`
funds approximately `N / 2` complete image+video parts; a normal new four-part story needs eight
units, before crash/provider retries. Set
`BACKGROUND_STORY_ENABLED=false` when the whole scheduler, including free text generation, must
remain off. If GigaChat is active, keep the intended `GIGACHAT_MODEL` explicitly; the current
example uses `GigaChat-3-Ultra`. Also raise the generated-volume processing reservation in
`.env.production`:

```env
STORAGE_ADMISSION_VIDEO_RESERVE_BYTES=268435456
```

Validate without printing secrets:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml config --quiet
```

The backend image runs as UID/GID `10001`. Before the first update from an older root-running
image, stop both writers so that the one-shot `volume-permissions` service can safely migrate
existing `generated_assets`, `backend_logs`, and `push_data` ownership:

```bash
cd /opt/gigagochi
git pull --ff-only origin main
docker compose --env-file .env.production -f docker-compose.prod.yml stop backend bot
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

The migration is idempotent and remains a dependency of `backend` for later updates. Once all
files are owned by `10001:10001`, it only scans the volume trees and changes nothing.

For subsequent updates:

```bash
cd /opt/gigagochi
git pull --ff-only origin main
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
docker image prune -f
```

For admin data-only updates (`backend/data/*`), production mounts managed data files/directories
from `./backend/data` into the backend image as read-only binds, while `push_data` still owns
`/app/data/push`. A faster update is enough:

```bash
cd /opt/gigagochi
git pull --ff-only origin main
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --no-build --force-recreate backend bot
```

## Local admin publish

The `/admin/speech` editor is intentionally local-only. To publish edited data files from the
local admin UI, enable publish in the local backend `.env` only:

```bash
ALLOW_DEV_TMA_AUTH=true
ADMIN_PUBLISH_ENABLED=true
ADMIN_PUBLISH_GIT_REMOTE=origin
ADMIN_PUBLISH_GIT_BRANCH=main
ADMIN_PUBLISH_SSH_TARGET=root@167.233.103.46
# optional when the default SSH agent/key is not enough
ADMIN_PUBLISH_SSH_KEY_PATH=~/.ssh/id_ed25519
ADMIN_PUBLISH_REMOTE_PATH=/opt/gigagochi
ADMIN_PUBLISH_HEALTH_URL=https://gigagochi.serega.works/health
ADMIN_SYNC_FROM_SERVER_ENABLED=true
```

The `Опубликовать` button saves dirty admin drafts, validates all managed `backend/data` files,
commits only those managed data paths, pushes `HEAD:main` to GitHub, then runs the fast data-only
update on Hetzner over SSH (`git pull --ff-only origin main` plus `up -d --no-build backend bot`)
and checks `/health`. With `ADMIN_SYNC_FROM_SERVER_ENABLED=true`, every `/admin/speech` load first
reads the current Git commit from Hetzner and refreshes the local managed data files before
returning the manifest. Keep `ADMIN_PUBLISH_ENABLED=false` and `ADMIN_SYNC_FROM_SERVER_ENABLED=false`
on the production server.

## Verify

```bash
curl -fsS https://gigagochi.serega.works/health
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=100 backend
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=100 bot
```

After a restore, also open several previously generated image/video URLs and confirm that an
already processed Telegram update is not accepted a second time. These checks exercise the
cross-volume consistency that `/health` alone cannot prove.

In BotFather and any Telegram bot menu settings, set the Mini App URL to:

```text
https://gigagochi.serega.works
```
