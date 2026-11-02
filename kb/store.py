from pathlib import Path

from kb.config import data_dir
from kb.text import canonical_url, chunks, content_hash, extract


def save_document(conn, *, url, title, platform, source_type, raw, mime,
                  source_id=None, run_id=None, reference=False):
    text, sections = extract(raw, mime, title)
    if len(text.strip()) < 40:
        raise ValueError("No extractable document text (OCR may be required)")
    fingerprint = content_hash(text)
    folder = data_dir() / "originals"
    folder.mkdir(exist_ok=True)
    path = folder / fingerprint
    try:
        with path.open("xb") as f:
            f.write(raw)
    except FileExistsError:
        pass
    with conn.transaction():
        doc = conn.execute("""INSERT INTO kb_documents(url,title,platform,source_type,is_reference)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT(url) DO UPDATE SET title=excluded.title
            RETURNING id""", (canonical_url(url), title[:1000], platform, source_type, reference)).fetchone()["id"]
        row = conn.execute("""INSERT INTO kb_versions(content_hash,text,raw_path,mime,run_id,status)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(content_hash) DO NOTHING RETURNING id""",
            (fingerprint, text, "originals/" + fingerprint, mime, run_id, "done" if reference else "new")).fetchone()
        created = row is not None
        version = row["id"] if row else conn.execute("SELECT id FROM kb_versions WHERE content_hash=%s", (fingerprint,)).fetchone()["id"]
        conn.execute("""INSERT INTO kb_document_versions(document_id,version_id,source_id) VALUES (%s,%s,%s)
            ON CONFLICT(document_id,version_id) DO UPDATE SET seen_at=now()""", (doc, version, source_id))
        if created:
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO kb_chunks(version_id,ordinal,locator,text) VALUES (%s,%s,%s,%s)",
                                [(version, i, loc, t) for i, (loc, t) in enumerate(chunks(sections))])
    return version, created
