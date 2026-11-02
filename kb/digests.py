from datetime import datetime
from zoneinfo import ZoneInfo

import db
from kb.config import integer


def create_digest(conn, now=None):
    import os
    now = now or datetime.now(ZoneInfo(os.getenv("DIGEST_TIMEZONE", "Europe/Moscow")))
    if now.hour < integer("DIGEST_HOUR", 9):
        return None
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(4209)")
        if conn.execute("SELECT 1 FROM kb_digests WHERE day=%s", (now.date(),)).fetchone():
            return None
        # Defer the digest while a recent collection and its analysis are in flight.
        busy = conn.execute("""SELECT EXISTS(SELECT 1 FROM kb_runs WHERE service='collector' AND status='running'
            AND started_at>now()-interval '30 minutes') OR EXISTS(SELECT 1 FROM kb_versions WHERE
            status IN ('new','processing','failed') AND attempts<%s AND created_at>now()-interval '2 hours') AS busy""",
            (integer("MAX_ATTEMPTS", 3),)).fetchone()["busy"]
        if busy:
            return None
        rows = conn.execute("""SELECT f.id,f.summary,f.analysis,d.url,d.platform FROM kb_findings f
            JOIN LATERAL(SELECT doc.url,doc.platform FROM kb_document_versions dv
                JOIN kb_documents doc ON doc.id=dv.document_id WHERE dv.version_id=f.version_id ORDER BY doc.id LIMIT 1)d ON true
            WHERE f.novelty AND NOT EXISTS(SELECT 1 FROM kb_digests g WHERE f.id=ANY(g.finding_ids)) ORDER BY f.id LIMIT 30""").fetchall()
        if not rows:
            return None
        lines = [f"Дайджест программирования анкет · {now:%d.%m.%Y}", f"Полезных обновлений: {len(rows)}"]
        for row in rows:
            a = row["analysis"]
            lines.append(f"\n• {row['platform']}: {row['summary']}\nЗадача: {a.get('task','')}\nРешение: {a.get('solution','')}\n{row['url']}")
        digest = conn.execute("INSERT INTO kb_digests(day,content,finding_ids) VALUES (%s,%s,%s) RETURNING id",
                             (now.date(), "\n".join(lines), [r["id"] for r in rows])).fetchone()["id"]
        conn.execute("""INSERT INTO kb_deliveries(digest_id,chat_id) SELECT %s,chat_id FROM kb_subscribers
            WHERE subscribed_at<=now() ON CONFLICT DO NOTHING""", (digest,))
        return digest


def deliver(conn, send):
    # A failed recipient never marks another person's digest as sent.
    rows = conn.execute("""SELECT d.digest_id,d.chat_id,g.content FROM kb_deliveries d JOIN kb_digests g ON g.id=d.digest_id
        JOIN kb_subscribers s ON s.chat_id=d.chat_id WHERE d.sent_at IS NULL AND d.retry_at<=now() AND d.attempts<10
        ORDER BY d.digest_id LIMIT 50""").fetchall()
    for row in rows:
        try:
            send(row["chat_id"], row["content"])
            conn.execute("UPDATE kb_deliveries SET sent_at=now(),attempts=attempts+1,error=NULL WHERE digest_id=%s AND chat_id=%s",
                         (row["digest_id"], row["chat_id"]))
        except Exception as error:
            conn.execute("UPDATE kb_deliveries SET attempts=attempts+1,error=%s,retry_at=now()+interval '10 minutes' WHERE digest_id=%s AND chat_id=%s",
                         (type(error).__name__, row["digest_id"], row["chat_id"]))
