import time

import db
from kb import analysis
from kb.config import integer
from kb.ingest import bootstrap
from kb.search import index_batch


def process_one(conn):
    row = analysis.claim(conn)
    if not row:
        return False
    run = db.start_run(conn, "worker")
    try:
        analysis.analyse(conn, row["id"])
        db.finish_run(conn, run, 1)
    except Exception as error:
        conn.execute("UPDATE kb_versions SET status='failed',lease_until=NULL,retry_at=now()+interval '15 minutes',error=%s WHERE id=%s",
                     (type(error).__name__ + ": " + str(error)[:300], row["id"]))
        db.finish_run(conn, run, error=type(error).__name__)
        db.log("worker", f"version {row['id']}: {type(error).__name__}")
    return True


def main():
    with db.connect() as conn:
        db.init_schema(conn)
        bootstrap(conn)
    while True:
        try:
            with db.connect() as conn:
                busy = process_one(conn)
                try:
                    indexed = index_batch(conn)
                except Exception as error:
                    db.log("worker", f"Embedding indexing: {type(error).__name__}")
                    indexed = 0
            if not busy and not indexed:
                time.sleep(integer("POLL_SECONDS", 10))
        except Exception as error:
            db.log("worker", type(error).__name__)
            time.sleep(10)
