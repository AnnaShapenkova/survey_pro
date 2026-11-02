"""Persistent interval scheduler: no extra Coolify cron configuration required."""
import time

import db
from kb.config import integer


def tick(conn):
    if not conn.execute("SELECT pg_try_advisory_lock(4211) AS locked").fetchone()["locked"]:
        return
    try:
        latest = conn.execute("SELECT started_at FROM kb_runs WHERE service='collector' ORDER BY id DESC LIMIT 1").fetchone()
        due = not latest or conn.execute("SELECT now() - %s > make_interval(secs => %s) AS due",
            (latest["started_at"], integer("COLLECT_INTERVAL_SECONDS", 86400))).fetchone()["due"]
        if due:
            from collector.main import collect
            collect(conn)
    finally:
        conn.execute("SELECT pg_advisory_unlock(4211)")


def main():
    while True:
        try:
            with db.connect() as conn:
                db.init_schema(conn)
                tick(conn)
        except Exception as error:
            db.log("scheduler", type(error).__name__)
        time.sleep(60)
