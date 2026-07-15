#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMIT_MESSAGE="${1:-}"
CHECK_MODE="${2:-fast}"

REMOTE="${DEPLOY_GIT_REMOTE:-origin}"
BRANCH="${DEPLOY_GIT_BRANCH:-main}"
SSH_TARGET="${DEPLOY_SSH_TARGET:-root@167.233.103.46}"
SSH_KEY="${DEPLOY_SSH_KEY:-${HOME}/.ssh/hermes_hetzner}"
REMOTE_PATH="${DEPLOY_REMOTE_PATH:-/opt/gigagochi}"
HEALTH_URL="${DEPLOY_HEALTH_URL:-https://gigagochi.serega.works/health}"

usage() {
  cat <<'EOF'
Usage: ./scripts/publish.sh "commit message" [fast|full]

  fast  Static checks, push main, build and deploy (default)
  full  Full backend/frontend tests before push and deploy
EOF
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Command not found: $1"
    exit 1
  fi
}

wait_for_health() {
  local attempt
  for ((attempt = 1; attempt <= 20; attempt += 1)); do
    if curl --fail --silent --show-error "$HEALTH_URL"; then
      echo
      return 0
    fi
    echo "Production health is not ready ($attempt/20)"
    sleep 3
  done
  return 1
}

if [ -z "$COMMIT_MESSAGE" ]; then
  usage
  exit 1
fi

if [ "$CHECK_MODE" != "fast" ] && [ "$CHECK_MODE" != "full" ]; then
  usage
  exit 1
fi

require_command git
require_command make
require_command ssh
require_command curl

cd "$ROOT_DIR"

CURRENT_BRANCH="$(git branch --show-current)"
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
  echo "Publish is allowed only from $BRANCH; current branch is $CURRENT_BRANCH"
  exit 1
fi

if [ ! -f "$SSH_KEY" ]; then
  echo "SSH key not found: $SSH_KEY"
  exit 1
fi

if [ "$CHECK_MODE" = "full" ]; then
  make check
else
  make check-fast
fi

git add -A
if git diff --cached --quiet; then
  echo "No local changes to commit"
else
  git diff --cached --stat
  git commit -m "$COMMIT_MESSAGE"
fi

git push "$REMOTE" "$BRANCH"

ssh -i "$SSH_KEY" "$SSH_TARGET" \
  "set -e; cd '$REMOTE_PATH'; git pull --ff-only '$REMOTE' '$BRANCH'; docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build; docker compose --env-file .env.production -f docker-compose.prod.yml ps"

wait_for_health
echo "Published $(git rev-parse --short HEAD) to $HEALTH_URL"
