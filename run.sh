#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
source venv/bin/activate
set -a; source .env; set +a
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
