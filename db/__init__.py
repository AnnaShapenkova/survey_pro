"""PostgreSQL connections, additive schema, and structured run history."""
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row, connect_timeout=10)


def init_schema(conn):
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(4207)")
        conn.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))


def log(service, message):
    print(f"[{service}] {message}", flush=True)


def start_run(conn, service):
    return conn.execute("INSERT INTO kb_runs(service) VALUES (%s) RETURNING id", (service,)).fetchone()["id"]


def finish_run(conn, run_id, items=0, error=None):
    conn.execute("UPDATE kb_runs SET status=%s, items=%s, note=%s, finished_at=now() WHERE id=%s",
                 ("failed" if error else "finished", items, str(error)[:1000] if error else "", run_id))
