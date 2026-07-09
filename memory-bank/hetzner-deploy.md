# Hetzner Deploy

Use this first for production deploy tasks.

## Production Target

- GitHub remote: `https://github.com/rivfadersp28/gigagochi.git`
- Branch: `main`
- Production domain: `https://gigagochi.serega.works`
- Health check: `https://gigagochi.serega.works/health`
- IPv4: `167.233.103.46`
- IPv6 prefix: `2a01:4f8:c015:8b05::/64`
- SSH target: `root@167.233.103.46`
- SSH key path used locally: `~/.ssh/hermes_hetzner`
- Server repo path: `/opt/gigagochi`

## Manual Fast Deploy

From local repo:

```bash
cd /Users/sergejegorov/tamagochi_tlg/tamagochi-main
git status --short
git push origin main
```

Deploy on Hetzner:

```bash
ssh -i ~/.ssh/hermes_hetzner root@167.233.103.46 'set -e; cd /opt/gigagochi; git pull --ff-only origin main; docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build; docker compose --env-file .env.production -f docker-compose.prod.yml ps'
curl -fsS https://gigagochi.serega.works/health
```

Admin data-only deploy is faster and should not rebuild images:

```bash
ssh -i ~/.ssh/hermes_hetzner root@167.233.103.46 'set -e; cd /opt/gigagochi; git pull --ff-only origin main; docker compose --env-file .env.production -f docker-compose.prod.yml up -d --no-build --force-recreate backend bot; docker compose --env-file .env.production -f docker-compose.prod.yml ps backend bot'
curl -fsS https://gigagochi.serega.works/health
```

Equivalent commands after SSH login:

```bash
cd /opt/gigagochi
git pull --ff-only origin main
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
docker compose --env-file .env.production -f docker-compose.prod.yml ps
docker image prune -f
curl -fsS https://gigagochi.serega.works/health
```

## Local Admin Publish

Local backend `.env` currently uses:

```bash
ADMIN_PUBLISH_ENABLED=true
ADMIN_PUBLISH_GIT_REMOTE=origin
ADMIN_PUBLISH_GIT_BRANCH=main
ADMIN_PUBLISH_SSH_TARGET=root@167.233.103.46
ADMIN_PUBLISH_SSH_KEY_PATH=~/.ssh/hermes_hetzner
ADMIN_PUBLISH_REMOTE_PATH=/opt/gigagochi
ADMIN_PUBLISH_HEALTH_URL=https://gigagochi.serega.works/health
ADMIN_PUBLISH_COMMAND_TIMEOUT_SECONDS=1200
ADMIN_SYNC_FROM_SERVER_ENABLED=true
```

Keep `ADMIN_PUBLISH_ENABLED=false` and `ADMIN_SYNC_FROM_SERVER_ENABLED=false`
on Hetzner production.

Local admin publish is data-only: it commits managed `backend/data` files, pulls
them on Hetzner, and runs `up -d --no-build --force-recreate backend bot`. Full
`--build` deploy is still required for backend/frontend code, dependencies,
Dockerfile, or compose changes that affect images.

## Caddy

The server already has a public Caddy container on ports `80` and `443`:
`bizzy-radio-caddy-1`. Do not start the compose `container-caddy` profile unless
that Caddy ownership is intentionally changed.

The app compose joins the existing public Docker network `bizzy-radio_default`
with aliases:

- `gigagochi-backend`
- `gigagochi-frontend`

## Logs

On Hetzner, from `/opt/gigagochi`:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=100 backend
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=100 bot
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=100 frontend
```

## Rules

- Use explicit `git pull --ff-only origin main`; server branch may not have
  upstream tracking.
- Admin data publish should use `up -d --no-build --force-recreate backend bot`,
  not a full image rebuild. `--force-recreate` is required so bind-mounted
  managed files are definitely visible inside running backend/bot containers.
- Do not write production admin data directly over SSH; publish through GitHub
  and the deploy pipeline so local, GitHub, and Hetzner stay aligned.
- Do not store tokens, private key contents, or real production `.env` secrets
  in memory-bank.
