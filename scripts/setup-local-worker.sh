#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN="${PYTHON_BIN:-/opt/homebrew/bin/python3.12}"
VENV_DIR="$PROJECT_ROOT/.venv-worker"

if [ ! -x "$PYTHON_BIN" ]; then
    echo "Python 3.12 не найден: $PYTHON_BIN" >&2
    exit 1
fi

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -e "$PROJECT_ROOT/worker" "pytest>=8,<9"

echo "Локальный worker установлен в $VENV_DIR"
