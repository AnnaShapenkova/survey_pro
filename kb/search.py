"""Hybrid lexical + multilingual semantic retrieval, with persisted embeddings."""
import math
import os
import re

import db
from kb import llm
from kb.config import integer


def cosine(a, b):
    if len(a) != len(b):
        return 0.0
    den = math.sqrt(sum(x*x for x in a) * sum(y*y for y in b))
    return sum(x*y for x, y in zip(a, b)) / den if den else 0.0


def index_batch(conn):
    model = os.getenv("EMBEDDING_MODEL", "").strip()
    if not model:
        return 0
    rows = conn.execute("SELECT id,text FROM kb_chunks WHERE embedding IS NULL OR embedding_model<>%s ORDER BY id LIMIT 24", (model,)).fetchall()
    if not rows:
        return 0
    vectors = llm.embed([r["text"] for r in rows])
    with conn.transaction():
        for row, vector in zip(rows, vectors):
            conn.execute("UPDATE kb_chunks SET embedding=%s,embedding_model=%s WHERE id=%s", (db.Jsonb(vector), model, row["id"]))
    return len(rows)


def search(conn, question, limit=8, reference=None, platform=None, semantic=True):
    tokens = re.findall(r"[\w]+", question.lower())[:25]
    if not tokens:
        return []
    tsquery = " | ".join(tokens)
    conditions, args = [], []
    if reference is not None:
        conditions.append("d.is_reference=%s")
        args.append(reference)
    if platform:
        conditions.append("d.platform=%s")
        args.append(platform)
    # Latest version per document, while preserving the historical versions in storage.
    where = " AND ".join(["dv.version_id=(SELECT x.version_id FROM kb_document_versions x WHERE x.document_id=d.id ORDER BY x.seen_at DESC,x.version_id DESC LIMIT 1)"] + conditions)
    base = f"""SELECT DISTINCT ON(c.id) c.id,c.version_id,c.text,c.locator,c.embedding,c.embedding_model,
        d.title,d.url,d.platform,d.source_type FROM kb_chunks c JOIN kb_document_versions dv ON dv.version_id=c.version_id
        JOIN kb_documents d ON d.id=dv.document_id WHERE {where}"""
    lexical = conn.execute(base + " AND (c.search_vector @@ to_tsquery('simple',%s) OR c.search_vector_ru @@ to_tsquery('russian',%s) OR c.text ILIKE %s) ORDER BY c.id DESC LIMIT 100", args + [tsquery, tsquery, "%" + question[:200] + "%"]).fetchall()
    ranked = {}
    for row in lexical:
        score = sum(row["text"].lower().count(t) for t in tokens)
        ranked[row["id"]] = (score, row)
    lex_sorted = sorted(ranked.values(), key=lambda x: x[0], reverse=True)
    scores = {r["id"]: 1/(60+i) for i, (_, r) in enumerate(lex_sorted, 1)}
    model = os.getenv("EMBEDDING_MODEL", "").strip()
    if semantic and model:
        try:
            vector = llm.embed([question])[0]
            rows = conn.execute(base + " AND c.embedding_model=%s AND c.embedding IS NOT NULL ORDER BY c.id DESC LIMIT %s", args + [model, integer("SEMANTIC_SCAN_LIMIT", 20000)]).fetchall()
            nearest = sorted(((cosine(vector, r["embedding"]), r) for r in rows), key=lambda x: x[0], reverse=True)[:30]
            for i, (similarity, row) in enumerate(nearest, 1):
                if similarity < 0.25:
                    continue
                scores[row["id"]] = scores.get(row["id"], 0) + 1/(60+i)
                ranked[row["id"]] = (similarity, row)
        except Exception as error:
            db.log("search", f"Semantic search unavailable, using lexical: {type(error).__name__}")
    selected, per_version = [], {}
    for chunk_id in sorted(scores, key=scores.get, reverse=True):
        row = dict(ranked[chunk_id][1])
        if per_version.get(row["version_id"], 0) >= 3:
            continue
        per_version[row["version_id"]] = per_version.get(row["version_id"], 0) + 1
        row.pop("embedding", None)
        selected.append(row)
        if len(selected) == limit:
            break
    return selected
