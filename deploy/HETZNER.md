# Hetzner production deploy

Production host:

- IPv4: `167.233.103.46`
- Domain: `gigagochi.serega.works`
- IPv6 prefix: `2a01:4f8:c015:8b05::/64`

`gigagochi.serega.works` must point to the server before Caddy can issue TLS certificates.
Use an `A` record for `167.233.103.46`. If you want IPv6 too, add an `AAAA` record for the
server's concrete IPv6 address, not the `/64` prefix.

Current observation from outside: `https://gigagochi.serega.works/health` responds with the
backend health check through Caddy. Do not start the container Caddy profile unless the existing
host Caddy configuration is intentionally replaced, otherwise ports `80` and `443` can conflict.

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
- `OPENROUTER_API_KEY`
- optional OpenAI keys only if `AI_PROVIDER=openai`

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

If you intentionally want this compose stack to own ports `80` and `443`, stop/disable the existing
host Caddy first, then start the opt-in container profile:

```bash
systemctl stop caddy
systemctl disable caddy
docker compose --env-file .env.production -f docker-compose.prod.yml --profile container-caddy up -d --build
```

PostgreSQL is not started by default because the current MVP keeps pet progress and chat history in
Telegram WebView `localStorage`. Start it only for legacy/post-MVP DB routes:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml --profile legacy-db up -d
```

## Update

```bash
cd /opt/gigagochi
git pull --ff-only origin main
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
docker image prune -f
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
```

The `Опубликовать` button saves dirty admin drafts, validates all managed `backend/data` files,
commits only those managed data paths, pushes `HEAD:main` to GitHub, then runs the same update
command on Hetzner over SSH (`git pull --ff-only origin main` plus compose rebuild) and checks
`/health`. Keep `ADMIN_PUBLISH_ENABLED=false` on the production server.

## Verify

```bash
curl -fsS https://gigagochi.serega.works/health
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=100 backend
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=100 bot
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=100 caddy
```

In BotFather and any Telegram bot menu settings, set the Mini App URL to:

```text
https://gigagochi.serega.works
```
