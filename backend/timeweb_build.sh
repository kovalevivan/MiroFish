#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

cd "$REPO_ROOT"

pip install --no-deps --extra-index-url https://download.pytorch.org/whl/cpu torch==2.9.1
pip install -r backend/requirements.timeweb.txt
pip install --no-deps ./backend
