#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
BIN="${POSTGRES_BIN:-$ROOT/runtime/postgres/usr/lib/postgresql/17/bin}"
export LD_LIBRARY_PATH="$ROOT/runtime/postgres/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
DATA="$ROOT/runtime/pgdata"
SOCKET="$ROOT/runtime/pgsocket"
mkdir -p "$SOCKET"
chmod 700 "$SOCKET"
if [ ! -f "$DATA/PG_VERSION" ]; then
  "$BIN/initdb" -D "$DATA" -U sales_owner --auth=trust --encoding=UTF8 --locale=C -L "$ROOT/runtime/postgres/usr/share/postgresql/17"
  cat >> "$DATA/postgresql.conf" <<EOF
listen_addresses = '127.0.0.1'
port = 55432
unix_socket_directories = ''
dynamic_shared_memory_type = mmap
timezone = 'UTC'
max_connections = 30
shared_buffers = '128MB'
EOF
fi
if ! "$BIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; then
  "$BIN/pg_ctl" -D "$DATA" -l "$ROOT/runtime/postgres.log" -w start
fi
"$BIN/psql" -h 127.0.0.1 -p 55432 -U sales_owner -d postgres -v ON_ERROR_STOP=1 <<'SQL'
SELECT 'CREATE ROLE sales_app LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='sales_app')\gexec
SELECT 'CREATE ROLE sales_reader LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='sales_reader')\gexec
SELECT 'CREATE DATABASE sales_agent OWNER sales_app' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='sales_agent')\gexec
SQL
"$BIN/psql" -h 127.0.0.1 -p 55432 -U sales_owner -d sales_agent -v ON_ERROR_STOP=1 -f scripts/schema.sql
