#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export AGENT_MODE=mock
export LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false
scripts/start_postgres.sh > runtime/test-postgres.log 2>&1
trap 'LD_LIBRARY_PATH="$PWD/runtime/postgres/usr/lib/x86_64-linux-gnu" runtime/postgres/usr/lib/postgresql/17/bin/pg_ctl -D runtime/pgdata -m fast -w stop >/dev/null 2>&1 || true' EXIT
.venv/bin/python scripts/import_data.py
.venv/bin/pytest -q "$@"
