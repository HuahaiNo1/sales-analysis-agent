#!/usr/bin/env bash
# One lifecycle for PostgreSQL, API, UI and optional local-browser tests.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p runtime
MODE="${1:-}"
if [[ "$MODE" == --test-e2e || "$MODE" == --test-backend ]]; then
  export AGENT_MODE=mock SALES_DISABLE_ENV_FILE=1 LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false
  TEST_BUDGET_DIR="$(mktemp -d)"
  export LLM_BUDGET_PATH="$TEST_BUDGET_DIR/llm-budget.sqlite3"
fi
scripts/start_postgres.sh > runtime/dev-postgres.log 2>&1
cleanup() {
  [[ -n "${API_PID:-}" ]] && kill "$API_PID" 2>/dev/null || true
  [[ -n "${UI_PID:-}" ]] && kill "$UI_PID" 2>/dev/null || true
  LD_LIBRARY_PATH="$PWD/runtime/postgres/usr/lib/x86_64-linux-gnu" runtime/postgres/usr/lib/postgresql/17/bin/pg_ctl -D runtime/pgdata -m fast -w stop >/dev/null 2>&1 || true
  [[ -n "${TEST_BUDGET_DIR:-}" ]] && rm -rf -- "$TEST_BUDGET_DIR" || true
}
trap cleanup EXIT INT TERM
.venv/bin/python scripts/import_data.py
if [[ "$MODE" == --test-backend ]]; then
  .venv/bin/pytest -q
  exit
fi
.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 > runtime/api.log 2>&1 &
API_PID=$!
(cd frontend && npm run dev -- --port 5173 --strictPort) > runtime/frontend.log 2>&1 &
UI_PID=$!
for _ in $(seq 1 100); do
  if curl --noproxy '*' -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && curl --noproxy '*' -fsS http://127.0.0.1:5173 >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null || ! kill -0 "$UI_PID" 2>/dev/null; then
    echo 'A service exited. See runtime/api.log and runtime/frontend.log.' >&2
    exit 1
  fi
  sleep .2
done
curl --noproxy '*' -fsS http://127.0.0.1:8000/api/health >/dev/null
printf '\nLocal services ready: UI http://127.0.0.1:5173, API http://127.0.0.1:8000\n'
printf 'Demo accounts: admin / demo123, analyst / demo123\n'
printf 'These are local URLs, not a public deployment. Ctrl+C stops all services.\n\n'
if [[ "$MODE" == --test-e2e ]]; then
  (cd frontend && npm run test:e2e)
else
  wait -n "$API_PID" "$UI_PID"
fi
