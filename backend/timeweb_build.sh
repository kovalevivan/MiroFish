#!/bin/sh
set -eu

cd /workspace

pip install --no-deps --extra-index-url https://download.pytorch.org/whl/cpu torch==2.9.1
pip install -r backend/requirements.timeweb.txt
pip install --no-deps ./backend
