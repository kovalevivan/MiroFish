#!/bin/sh
set -eu

pip install uv

cd /workspace/backend

uv export --frozen --format requirements-txt --no-hashes > requirements.lock

grep -v '^-e \.$' requirements.lock | \
  grep -v '^torch==' | \
  grep -v '^nvidia_' > requirements.timeweb.txt

pip install --index-url https://download.pytorch.org/whl/cpu torch==2.9.1
pip install -r requirements.timeweb.txt
pip install --no-deps .
