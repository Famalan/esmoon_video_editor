#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

export POSTGRES_HOST=127.0.0.1
export REDIS_URL=redis://127.0.0.1:6379/0
export MINIO_ENDPOINT=http://127.0.0.1:9000
export CODEX_CLI_PATH="${CODEX_CLI_PATH:-/Applications/ChatGPT.app/Contents/Resources/codex}"
export CODEX_MODEL=gpt-6-astra
export CODEX_REASONING_EFFORT=medium
export PYTHONPATH="$PROJECT_ROOT/worker:$PROJECT_ROOT/shared${PYTHONPATH:+:$PYTHONPATH}"
export WHISPER_CACHE_DIR="${WHISPER_CACHE_DIR:-$PROJECT_ROOT/.cache/whisper}"

if [ ! -x "$PROJECT_ROOT/.venv-worker/bin/celery" ]; then
    echo "Worker не установлен. Сначала выполните ./scripts/setup-local-worker.sh" >&2
    exit 1
fi

mkdir -p "$WHISPER_CACHE_DIR"
cd "$PROJECT_ROOT/worker"

exec "$PROJECT_ROOT/.venv-worker/bin/celery" \
    -A worker.celery_app worker --loglevel=info --concurrency=1
