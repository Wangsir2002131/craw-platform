#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
LIMIT="${LIMIT:-100}"
INTERVAL="${INTERVAL:-5}"
HEARTBEAT_INTERVAL="${HEARTBEAT_INTERVAL:-10}"
HEALTH_CHECK_INTERVAL="${HEALTH_CHECK_INTERVAL:-30}"
STALE_AFTER="${STALE_AFTER:-60}"

cd "$ROOT_DIR"

exec py -m platform.main_server \
  --forever \
  --host "$HOST" \
  --port "$PORT" \
  --limit "$LIMIT" \
  --interval "$INTERVAL" \
  --heartbeat-interval "$HEARTBEAT_INTERVAL" \
  --health-check-interval "$HEALTH_CHECK_INTERVAL" \
  --stale-after "$STALE_AFTER"
