#!/bin/sh
# One entry point. Run from any directory. Only this project's worker is managed.
set -eu
PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"
CODEX_BIN="${CODEX_CLI_PATH:-/Applications/ChatGPT.app/Contents/Resources/codex}"
for executable in docker ffmpeg ffprobe; do
    command -v "$executable" >/dev/null || { echo "Не найден $executable" >&2; exit 1; }
done
[ -f .env ] || { echo 'Создайте .env из .env.example и настройте локальные службы.' >&2; exit 1; }
"$CODEX_BIN" login status
if [ ! -x .venv-worker/bin/celery ]; then
    ./scripts/setup-local-worker.sh
fi
.venv-worker/bin/python -c 'import reportlab, sqlalchemy, celery' || {
    echo 'Обновите зависимости: ./scripts/setup-local-worker.sh' >&2; exit 1;
}
mkdir -p .cache/backups .cache/run
# Docker --wait is bounded; failed readiness reports which container needs attention.
docker compose up -d --wait --wait-timeout 60 postgres redis minio
# Build while the existing application is still available. Runtime-only starts can
# reuse installed images when dependencies have not changed.
needs_build=0
for runtime_image in $(docker compose config --images api web); do
    docker image inspect "$runtime_image" >/dev/null 2>&1 || needs_build=1
done
if [ "${1:-}" = "--build" ] || { [ "$needs_build" = 1 ] && [ "${1:-}" != "--no-build" ]; }; then
    docker compose build api web
fi
# Wait for jobs before changing a shared schema or restarting the process.
waited=0
while :; do
    active_jobs=$(docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM jobs WHERE status = '"'"'running'"'"' AND (last_activity_at IS NULL OR last_activity_at > now() - interval '"'"'2 minutes'"'"')"' 2>/dev/null || echo 0)
    active_exports=0
    worker_ready=$(docker compose exec -T redis redis-cli GET video-slicer:worker-ready)
    if [ -n "$worker_ready" ]; then
        active_exports=$(docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM exports WHERE status = ('"'"'processing'"'"')"' 2>/dev/null || echo 0)
    fi
    active=$((active_jobs+active_exports))
    [ "$active" = 0 ] && break
    if [ "$waited" -ge 1800 ]; then
        echo 'Есть незавершённые задания. Обновление не начато; проверьте их состояние в приложении.' >&2
        exit 1
    fi
    if [ $((waited % 60)) -eq 0 ]; then
        echo "Ждём завершения заданий: $active"
    fi
    sleep 5
    waited=$((waited+5))
done
if [ -f .cache/run/worker.pid ]; then
    worker_pid=$(cat .cache/run/worker.pid)
    if kill -0 "$worker_pid" 2>/dev/null; then
        command_line=$(ps -p "$worker_pid" -o command=)
        case "$command_line" in
            *"$PROJECT_ROOT/.venv-worker/bin/celery"*) kill -TERM "$worker_pid" ;;
            *) echo 'PID-файл принадлежит другому процессу; проверьте .cache/run/worker.pid.' >&2; exit 1 ;;
        esac
        remaining=30
        while kill -0 "$worker_pid" 2>/dev/null && [ "$remaining" -gt 0 ]; do sleep 1; remaining=$((remaining-1)); done
        kill -0 "$worker_pid" 2>/dev/null && { echo 'Обработчик ещё завершает работу.' >&2; exit 1; }
    fi
fi
backup=".cache/backups/before-start-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup"
docker compose exec -T postgres pg_restore --list < "$backup" >/dev/null
# Readers and writers use the new schema together. Originals and previous revisions are retained.
docker compose stop api web
docker compose run --rm --no-deps api alembic upgrade head
docker compose up -d api web
# Create the bucket before readiness, including on a completely fresh installation.
docker compose exec -T api python -c 'from app.services.storage import ensure_bucket; ensure_bucket()'
# A new process session survives both terminal closure and desktop task completion.
.venv-worker/bin/python - <<'WORKER'
from pathlib import Path
import subprocess
with Path('.cache/run/worker.log').open('ab') as log:
    process=subprocess.Popen(['./scripts/run-worker-local.sh'],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
Path('.cache/run/worker.pid').write_text(str(process.pid)+'\n')
WORKER
.venv-worker/bin/python - <<'PY'
import json,time,urllib.request
from dotenv import dotenv_values
port=dotenv_values('.env').get('API_PORT','8000')
for _ in range(45):
    try:
        with urllib.request.urlopen(f'http://localhost:{port}/health/ready',timeout=5) as response:
            state=json.load(response)
        if state['ready']:
            print('Video Slicer готов: http://localhost:'+dotenv_values('.env').get('WEB_PORT','3000'))
            break
    except Exception:
        pass
    time.sleep(1)
else:
    raise SystemExit('Службы не готовы. Диагностика: /health/ready; журнал: .cache/run/worker.log')
PY
