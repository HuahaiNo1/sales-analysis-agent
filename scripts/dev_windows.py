#!/usr/bin/env python3
r"""Windows localhost demo lifecycle; uses an existing PostgreSQL 17 installation.

Run with .venv\Scripts\python.exe. No installer, Windows service, firewall, or
system database is managed here. The demo cluster lives under ignored runtime/.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
DATABASE_ENV = {
    "DATABASE_URL": "postgresql://sales_app@127.0.0.1:55432/sales_agent",
    "ANALYTICS_DATABASE_URL": "postgresql://sales_reader@127.0.0.1:55432/sales_agent",
    "IMPORT_DATABASE_URL": "postgresql://sales_owner@127.0.0.1:55432/sales_agent",
}
BOOTSTRAP_SQL = r"""
SELECT 'CREATE ROLE sales_app LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='sales_app')\gexec
SELECT 'CREATE ROLE sales_reader LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='sales_reader')\gexec
SELECT 'CREATE DATABASE sales_agent OWNER sales_app' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='sales_agent')\gexec
"""


def postgres_bin() -> Path:
    default = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PostgreSQL/17/bin"
    folder = Path(os.environ.get("POSTGRES_BIN", str(default))).resolve()
    for name in ("postgres", "initdb", "pg_ctl", "psql"):
        if not (folder / f"{name}.exe").is_file():
            raise RuntimeError(
                "PostgreSQL 17 binaries not found. Install from "
                "https://www.postgresql.org/download/windows/ and set POSTGRES_BIN to its bin folder."
            )
    version = subprocess.check_output([folder / "postgres.exe", "--version"], text=True)
    if not version.strip().startswith("postgres (PostgreSQL) 17."):
        raise RuntimeError("The demo requires PostgreSQL 17; check POSTGRES_BIN.")
    return folder


def require_free_port(port: int) -> None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            pass
    except OSError:
        return
    raise RuntimeError(f"127.0.0.1:{port} is already in use; stop that service before launching this demo.")


@contextlib.contextmanager
def local_database(bin_dir: Path, env: dict[str, str], log):
    data = RUNTIME / "pgdata-windows"
    control = [str(bin_dir / "pg_ctl.exe"), "-D", str(data)]
    started = False
    try:
        if not (data / "PG_VERSION").exists():
            require_free_port(55432)
            subprocess.run(
                [bin_dir / "initdb.exe", "-D", data, "-U", "sales_owner",
                 "--auth=trust", "--encoding=UTF8", "--locale=C"],
                check=True, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            with (data / "postgresql.conf").open("a", encoding="utf-8", newline="\n") as config:
                config.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\n"
                             "unix_socket_directories = ''\ntimezone = 'UTC'\n"
                             "max_connections = 30\nshared_buffers = '128MB'\n")
        elif (data / "PG_VERSION").read_text().strip() != "17":
            raise RuntimeError("runtime/pgdata-windows belongs to another PostgreSQL major version.")
        status = subprocess.run(control + ["status"], env=env, stdout=log, stderr=subprocess.STDOUT)
        if status.returncode != 0:
            require_free_port(55432)
            started = True
            subprocess.run(control + ["-l", str(RUNTIME / "postgres-windows.log"), "-w", "start"],
                           check=True, env=env, stdout=log, stderr=subprocess.STDOUT)
        psql = [str(bin_dir / "psql.exe"), "-X", "-h", "127.0.0.1", "-p", "55432",
                "-U", "sales_owner", "-v", "ON_ERROR_STOP=1"]
        subprocess.run(psql + ["-d", "postgres"], input=BOOTSTRAP_SQL, text=True,
                       check=True, env=env, stdout=log, stderr=subprocess.STDOUT)
        subprocess.run(psql + ["-d", "sales_agent", "-f", str(ROOT / "scripts/schema.sql")],
                       check=True, env=env, stdout=log, stderr=subprocess.STDOUT)
        yield
    finally:
        # Never stop a cluster that was already running when this command began.
        if started:
            subprocess.run(control + ["-m", "fast", "-w", "stop"],
                           env=env, stdout=log, stderr=subprocess.STDOUT)


def wait_services(children, timeout: float = 60) -> None:
    opener = build_opener(ProxyHandler({}))  # Local health checks must stay on loopback.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(child.poll() is not None for child in children):
            raise RuntimeError("A service exited. See runtime/api.log and runtime/frontend.log.")
        try:
            for url in ("http://127.0.0.1:8000/api/health", "http://127.0.0.1:5173"):
                with opener.open(url, timeout=1) as response:
                    if response.status != 200:
                        raise URLError("Service is not ready")
            return
        except (OSError, URLError):
            time.sleep(0.2)
    raise RuntimeError("Services did not become ready in 60 seconds; see runtime logs.")


def stop_children(children) -> None:
    for child in reversed(children):
        if child.poll() is None:
            # Only terminate process trees created by this invocation (including Vite's esbuild).
            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            child.wait(timeout=15)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--test-backend", action="store_true")
    modes.add_argument("--test-e2e", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Use scripts/dev.sh on the supported Debian environment; this launcher is for Windows.")
    python = ROOT / ".venv/Scripts/python.exe"
    if not python.is_file():
        raise RuntimeError("Run uv sync --frozen --python 3.12 from the project root first.")
    if not (ROOT / "data/processed/manifest.json").is_file():
        raise RuntimeError("Generate the official data first: python scripts/contoso_generate.py")
    bin_dir = postgres_bin()
    env = os.environ.copy()
    env.update(DATABASE_ENV)
    env.update(LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    if args.test_backend or args.test_e2e:
        env["AGENT_MODE"] = "mock"
        env["SALES_DISABLE_ENV_FILE"] = "1"
    children = []
    RUNTIME.mkdir(exist_ok=True)
    with contextlib.ExitStack() as stack:
        if args.test_backend or args.test_e2e:
            test_budget_dir = stack.enter_context(tempfile.TemporaryDirectory(prefix="sales-test-budget-"))
            env["LLM_BUDGET_PATH"] = str(Path(test_budget_dir) / "llm-budget.sqlite3")
        if not args.test_backend:
            node = shutil.which("node")
            vite = ROOT / "frontend/node_modules/vite/bin/vite.js"
            if not node or not vite.is_file():
                raise RuntimeError("Install supported Node.js and run npm --prefix frontend ci first.")
            for port in (8000, 5173):
                require_free_port(port)
        pg_log = stack.enter_context((RUNTIME / "dev-postgres.log").open("w", encoding="utf-8"))
        stack.enter_context(local_database(bin_dir, env, pg_log))
        subprocess.run([python, ROOT / "scripts/import_data.py"], cwd=ROOT, env=env, check=True)
        if args.test_backend:
            return subprocess.run([python, "-m", "pytest", "-q"], cwd=ROOT, env=env).returncode
        try:
            for command, cwd, name in (
                ([python, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
                  "--host", "127.0.0.1", "--port", "8000"], ROOT, "api"),
                ([node, vite, "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
                 ROOT / "frontend", "frontend"),
            ):
                log = stack.enter_context((RUNTIME / f"{name}.log").open("w", encoding="utf-8"))
                children.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                                 stderr=subprocess.STDOUT,
                                                 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP))
            wait_services(children)
            print("Local UI: http://127.0.0.1:5173 | API: http://127.0.0.1:8000")
            print("Demo: admin / demo123 or analyst / demo123. Ctrl+C stops services started here.")
            if args.test_e2e:
                return subprocess.run([node, ROOT / "frontend/node_modules/@playwright/test/cli.js", "test"],
                                      cwd=ROOT / "frontend", env=env).returncode
            while all(child.poll() is None for child in children):
                time.sleep(0.5)
            raise RuntimeError("A service exited; see runtime logs.")
        finally:
            stop_children(children)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
