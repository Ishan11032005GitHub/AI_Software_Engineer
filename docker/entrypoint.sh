#!/usr/bin/env bash
set -euo pipefail

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "❌ GITHUB_TOKEN is not set. Export it or pass as -e GITHUB_TOKEN=..."
  exit 1
fi

if [ -z "${TARGET_REPO:-}" ]; then
  echo "❌ TARGET_REPO is not set. Example: -e TARGET_REPO='owner/repo'"
  exit 1
fi

echo "📦 Running AutoTriage agent on repo: ${TARGET_REPO}"
echo "🗄  SQLite path: ${SQLITE_PATH:-/app/data/agent.db}"

# Just in case, ensure data dir exists
mkdir -p "$(dirname "${SQLITE_PATH:-/app/data/agent.db"} )"

python -m app.main "${TARGET_REPO}"
