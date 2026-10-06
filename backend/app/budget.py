"""Persistent, process-safe budget using the provider's full context ceiling.

Every call reserves the worst-case model input plus capped output before I/O.
Only a successful response with strictly valid usage settles that reservation.
Ambiguous, failed, invalid-usage and legacy calls keep their complete reserve.
All amounts are integer micro-yuan; no local tokenizer estimate is trusted.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HARD_CAP_MICROYUAN = 4_800_000
MAX_OUTPUT_TOKENS = 2048
MODEL_CONTEXT_TOKENS = 1_048_576
# Compatibility alias: this now means the documented provider context ceiling.
MAX_INPUT_BOUND = MODEL_CONTEXT_TOKENS
# Peak cache-miss CNY rates checked against official docs on 2026-10-06.
# One micro-yuan/token is one yuan/million tokens.
INPUT_MICROYUAN_PER_TOKEN = 2
OUTPUT_MICROYUAN_PER_TOKEN = 8
FULL_RESERVATION_MICROYUAN = (
    MODEL_CONTEXT_TOKENS * INPUT_MICROYUAN_PER_TOKEN + MAX_OUTPUT_TOKENS * OUTPUT_MICROYUAN_PER_TOKEN
)
ACCOUNTING_VERSION = 2


class BudgetExceeded(RuntimeError):
    code = "BUDGET_EXCEEDED"


class BudgetIntegrityError(RuntimeError):
    code = "BUDGET_INTEGRITY_ERROR"


def _token_count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _storable_count(value: Any) -> int | None:
    return value if _token_count(value) and value <= 2**63 - 1 else None


class BudgetGuard:
    def __init__(self, path: str | Path, cap_microyuan: int = HARD_CAP_MICROYUAN):
        if type(cap_microyuan) is not int or not 0 < cap_microyuan <= HARD_CAP_MICROYUAN:
            raise ValueError("Budget must be a positive integer of no more than RMB 4.8")
        self.path = Path(path)
        self.cap_microyuan = cap_microyuan
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS budget_meta (id INTEGER PRIMARY KEY CHECK(id=1), cap INTEGER NOT NULL, blocked INTEGER NOT NULL DEFAULT 0)"
            )
            conn.execute("INSERT OR IGNORE INTO budget_meta(id,cap) VALUES (1,?)", (cap_microyuan,))
            # A process may lower the shared cap, but never raise an existing one.
            conn.execute("UPDATE budget_meta SET cap=MIN(cap,?) WHERE id=1", (cap_microyuan,))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, role TEXT NOT NULL, input_bound INTEGER NOT NULL, output_bound INTEGER NOT NULL, reserved INTEGER NOT NULL, outcome TEXT NOT NULL, input_used INTEGER, output_used INTEGER, settled_microyuan INTEGER, accounting_version INTEGER NOT NULL DEFAULT 1)"
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(reservations)")}
            if "settled_microyuan" not in columns:
                conn.execute("ALTER TABLE reservations ADD COLUMN settled_microyuan INTEGER")
            if "accounting_version" not in columns:
                conn.execute(
                    "ALTER TABLE reservations ADD COLUMN accounting_version INTEGER NOT NULL DEFAULT 1"
                )
            # Never erase, settle or trust older byte-estimated reservations.
            # Preserve their original audit fields while increasing exposure to
            # at least the complete current per-call reserve.
            conn.execute(
                "UPDATE reservations SET reserved=MAX(reserved,?),settled_microyuan=NULL WHERE accounting_version IS NULL OR accounting_version<>?",
                (FULL_RESERVATION_MICROYUAN, ACCOUNTING_VERSION),
            )
            exposure = conn.execute(
                "SELECT COALESCE(SUM(COALESCE(settled_microyuan,reserved)),0) FROM reservations"
            ).fetchone()[0]
            cap = conn.execute("SELECT cap FROM budget_meta WHERE id=1").fetchone()[0]
            if exposure > cap:
                conn.execute("UPDATE budget_meta SET blocked=1 WHERE id=1")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def input_bound(payload: Any) -> int:
        """Reserve the full provider context, irrespective of local text size."""
        return MODEL_CONTEXT_TOKENS

    def reserve(self, payload: Any, role: str) -> str:
        reservation_id = uuid.uuid4().hex
        with self._db() as conn:
            cap, blocked = conn.execute("SELECT cap,blocked FROM budget_meta WHERE id=1").fetchone()
            if blocked:
                raise BudgetIntegrityError("预算账本已安全锁定；需要人工核查")
            exposure = conn.execute(
                "SELECT COALESCE(SUM(COALESCE(settled_microyuan,reserved)),0) FROM reservations"
            ).fetchone()[0]
            if exposure + FULL_RESERVATION_MICROYUAN > cap:
                raise BudgetExceeded("剩余额度不足以预留完整上下文费用；未发送新的模型请求")
            conn.execute(
                "INSERT INTO reservations (id,created_at,role,input_bound,output_bound,reserved,outcome,input_used,output_used,settled_microyuan,accounting_version) VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,?)",
                (
                    reservation_id,
                    datetime.now(UTC).isoformat(),
                    role,
                    MODEL_CONTEXT_TOKENS,
                    MAX_OUTPUT_TOKENS,
                    FULL_RESERVATION_MICROYUAN,
                    "reserved",
                    ACCOUNTING_VERSION,
                ),
            )
        return reservation_id

    def finish(self, reservation_id: str, usage: dict[str, Any] | None, outcome: str = "succeeded") -> None:
        usage_dict = usage if isinstance(usage, dict) else {}
        input_used, output_used = usage_dict.get("input_tokens"), usage_dict.get("output_tokens")
        valid = _token_count(input_used) and _token_count(output_used)
        exceeded = (_token_count(input_used) and input_used > MODEL_CONTEXT_TOKENS) or (
            _token_count(output_used) and output_used > MAX_OUTPUT_TOKENS
        )
        if "total_tokens" in usage_dict:
            total = usage_dict["total_tokens"]
            valid = valid and _token_count(total) and total == input_used + output_used
        valid = valid and not exceeded
        with self._db() as conn:
            row = conn.execute(
                "SELECT outcome,accounting_version,settled_microyuan,input_used,output_used FROM reservations WHERE id=?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise BudgetIntegrityError("Unknown reservation")
            if exceeded:
                conn.execute("UPDATE budget_meta SET blocked=1 WHERE id=1")
                # Even a conflicting late overrun can never reduce exposure.
                conn.execute(
                    "UPDATE reservations SET outcome='uncertain',settled_microyuan=NULL WHERE id=?",
                    (reservation_id,),
                )
            elif row[2] is not None:
                if outcome != "succeeded" or not valid or (input_used, output_used) != (row[3], row[4]):
                    # Conflicting completion records cannot release any funds.
                    conn.execute("UPDATE budget_meta SET blocked=1 WHERE id=1")
                    conn.execute(
                        "UPDATE reservations SET outcome='uncertain',settled_microyuan=NULL WHERE id=?",
                        (reservation_id,),
                    )
                    exceeded = True
                # An identical successful completion is safely idempotent.
            elif row[0] == "reserved" and row[1] == ACCOUNTING_VERSION:
                if outcome == "succeeded" and valid:
                    settled = (
                        input_used * INPUT_MICROYUAN_PER_TOKEN + output_used * OUTPUT_MICROYUAN_PER_TOKEN
                    )
                    conn.execute(
                        "UPDATE reservations SET outcome='succeeded',input_used=?,output_used=?,settled_microyuan=? WHERE id=?",
                        (input_used, output_used, settled, reservation_id),
                    )
                else:
                    conn.execute(
                        "UPDATE reservations SET outcome='uncertain',input_used=?,output_used=? WHERE id=?",
                        (_storable_count(input_used), _storable_count(output_used), reservation_id),
                    )
            # Uncertain or legacy rows remain reserved, including late replies.
        if exceeded:
            raise BudgetIntegrityError("实际用量超界或结算冲突；后续模型调用已锁定")

    def snapshot(self) -> dict[str, Any]:
        with self._db() as conn:
            cap, blocked = conn.execute("SELECT cap,blocked FROM budget_meta WHERE id=1").fetchone()
            spent, pending, count = conn.execute(
                "SELECT COALESCE(SUM(settled_microyuan),0),COALESCE(SUM(CASE WHEN settled_microyuan IS NULL THEN reserved ELSE 0 END),0),COUNT(*) FROM reservations"
            ).fetchone()
        exposure = spent + pending
        return {
            "cap_rmb": cap / 1_000_000,
            "spent_rmb": spent / 1_000_000,
            "reserved_rmb": pending / 1_000_000,
            "exposure_rmb": exposure / 1_000_000,
            "remaining_rmb": max(0, cap - exposure) / 1_000_000,
            "calls_reserved": count,
            "blocked": bool(blocked),
            "accounting": "full_context_reservation_then_validated_peak_rate_settlement",
            "per_call_reserve_rmb": FULL_RESERVATION_MICROYUAN / 1_000_000,
            "context_token_ceiling": MODEL_CONTEXT_TOKENS,
            "pricing_checked_at": "2026-10-06",
            "input_rmb_per_million": 2,
            "output_rmb_per_million": 8,
        }


def shared_budget() -> BudgetGuard:
    default = Path(__file__).resolve().parents[2] / "runtime" / "llm-budget.sqlite3"
    return BudgetGuard(os.environ.get("LLM_BUDGET_PATH", str(default)))
