import json
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256

import psycopg
from psycopg.rows import dict_row

from .config import settings


@dataclass(frozen=True)
class Identity:
    subject: str
    display_name: str
    allowed_store_ids: tuple[int, ...]
    scope_label: str

    @property
    def scope_hash(self):
        return sha256(json.dumps([self.subject, self.allowed_store_ids, "scope-v1"]).encode()).hexdigest()


@contextmanager
def connect():
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


@contextmanager
def analytics(identity: Identity):
    with psycopg.connect(settings.analytics_database_url, row_factory=dict_row) as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute(
            "SELECT set_config('app.allowed_stores', %s, true)",
            [",".join(str(x) for x in identity.allowed_store_ids)],
        )
        conn.execute("SET LOCAL statement_timeout = '10s'")
        yield conn


def identity_for(subject: str) -> Identity:
    if subject not in {"admin", "analyst"}:
        raise ValueError("Unknown demo account")
    with connect() as conn:
        rows = conn.execute("SELECT store_key FROM dim_store ORDER BY store_key").fetchall()
    keys = [r["store_key"] for r in rows]
    if subject == "analyst":
        # A static deterministic subset is an application demo scope, not a claim about source tenants.
        keys = [k for k in keys if k > 0][:3]
    return Identity(
        subject,
        "全门店演示账号" if subject == "admin" else "受限门店演示账号",
        tuple(keys),
        "全部门店" if subject == "admin" else "仅授权 3 家门店",
    )


def dataset_metadata():
    with connect() as conn:
        row = conn.execute(
            "SELECT metadata FROM dataset_versions ORDER BY published_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        raise RuntimeError("DATASET_NOT_READY")
    return row["metadata"]
