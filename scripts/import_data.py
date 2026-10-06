#!/usr/bin/env python3
"""Load verified official generator CSV output atomically into the five canonical tables."""

import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import psycopg
from app.config import ROOT, settings
from psycopg import sql
from psycopg.types.json import Jsonb

TABLES = ["dim_product", "dim_store", "dim_customer_geo", "dim_date", "fact_sales"]


def main():
    folder = ROOT / "data" / "processed"
    manifest = json.loads((folder / "manifest.json").read_text())
    if manifest["status"] != "passed" or manifest["data_version"] != "contoso-v2-2023-2025-692e0347d360":
        raise RuntimeError("Unverified or unexpected data version")
    for filename, expected in manifest["processed_files"].items():
        if hashlib.sha256((folder / filename).read_bytes()).hexdigest() != expected["sha256"]:
            raise RuntimeError(f"Immutable data checksum mismatch: {filename}")
    with psycopg.connect(settings.import_database_url) as conn:
        existing = conn.execute("SELECT count(*) FROM fact_sales").fetchone()[0]
        if existing:
            print(f"Dataset already imported ({existing} sales lines); preserving immutable snapshot.")
            return
        for table in TABLES:
            path = folder / f"{table}.csv"
            with path.open(newline="", encoding="utf-8-sig") as f:
                columns = next(csv.reader(f))
                command = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT CSV, NULL '')").format(
                    sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns))
                )
                with conn.cursor().copy(command) as copy:
                    while chunk := f.read(1024 * 1024):
                        copy.write(chunk)
            print(f"Imported {table}")
        inconsistent = conn.execute("""SELECT count(*) FROM (SELECT order_key FROM fact_sales GROUP BY order_key
          HAVING count(DISTINCT (order_date, customer_key, store_key, currency_code)) <> 1) x""").fetchone()[
            0
        ]
        if inconsistent:
            raise RuntimeError(f"{inconsistent} inconsistent orders; import rolled back")
        counts = dict(
            zip(
                ["sales_lines", "orders", "start", "last_date"],
                conn.execute("""SELECT count(*),
          count(DISTINCT order_key),min(order_date)::text,max(order_date)::text FROM fact_sales""").fetchone(),
            )
        )
        end = conn.execute("SELECT (max(order_date)+1)::text FROM fact_sales").fetchone()[0]
        metadata = {
            "dataset_id": "contoso_v2",
            "dataset_version": "contoso-v2-2023-2025-692e0347d360",
            "metric_version": "metrics_v1",
            "cleaning_version": "normalization-v1",
            "source": "SQLBI Contoso Data Generator V2",
            "source_url": "https://github.com/sql-bi/Contoso-Data-Generator-V2",
            "source_commit": "eaeb57a9eaa6ad0cdab4fb527552102685434ee0",
            "simulated": True,
            "verified": True,
            "currency": "USD",
            "manifest_sha256": hashlib.sha256((folder / "manifest.json").read_bytes()).hexdigest(),
            "coverage": {"start": counts.pop("start"), "end": end},
            **counts,
        }
        conn.execute(
            "INSERT INTO dataset_versions (id,metadata) VALUES (%s,%s)",
            [metadata["dataset_version"], Jsonb(metadata)],
        )
        conn.execute("ANALYZE")
        print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
