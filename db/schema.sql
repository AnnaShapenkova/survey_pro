-- New names deliberately leave all tables and data from upstream intact.
CREATE TABLE IF NOT EXISTS kb_runs (
    id BIGSERIAL PRIMARY KEY, service TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ,
    items INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS kb_sources (
    id BIGSERIAL PRIMARY KEY, key TEXT NOT NULL UNIQUE, config JSONB NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true, checked_at TIMESTAMPTZ, error TEXT
);
CREATE TABLE IF NOT EXISTS kb_documents (
    id BIGSERIAL PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
    platform TEXT NOT NULL, source_type TEXT NOT NULL, is_reference BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS kb_versions (
    id BIGSERIAL PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE, text TEXT NOT NULL,
    raw_path TEXT NOT NULL, mime TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new','processing','done','skipped','failed')),
    attempts INTEGER NOT NULL DEFAULT 0, retry_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until TIMESTAMPTZ, error TEXT, run_id BIGINT REFERENCES kb_runs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS kb_document_versions (
    document_id BIGINT NOT NULL REFERENCES kb_documents(id),
    version_id BIGINT NOT NULL REFERENCES kb_versions(id),
    source_id BIGINT REFERENCES kb_sources(id), seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, version_id)
);
CREATE TABLE IF NOT EXISTS kb_chunks (
    id BIGSERIAL PRIMARY KEY, version_id BIGINT NOT NULL REFERENCES kb_versions(id),
    ordinal INTEGER NOT NULL, locator TEXT NOT NULL, text TEXT NOT NULL,
    embedding JSONB, embedding_model TEXT,
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    UNIQUE(version_id, ordinal)
);
CREATE INDEX IF NOT EXISTS kb_document_versions_latest ON kb_document_versions(document_id,seen_at DESC);
CREATE INDEX IF NOT EXISTS kb_chunks_search ON kb_chunks USING gin(search_vector);
ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS search_vector_ru TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('russian', text)) STORED;
CREATE INDEX IF NOT EXISTS kb_chunks_search_ru ON kb_chunks USING gin(search_vector_ru);
CREATE INDEX IF NOT EXISTS kb_versions_queue ON kb_versions(status, retry_at);
CREATE TABLE IF NOT EXISTS kb_findings (
    id BIGSERIAL PRIMARY KEY, version_id BIGINT NOT NULL REFERENCES kb_versions(id),
    analysis JSONB NOT NULL, summary TEXT NOT NULL, novelty BOOLEAN NOT NULL,
    fingerprint TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(version_id, model, prompt_version)
);
CREATE TABLE IF NOT EXISTS kb_subscribers (
    chat_id BIGINT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', subscribed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS kb_digests (
    id BIGSERIAL PRIMARY KEY, day DATE NOT NULL UNIQUE, content TEXT NOT NULL,
    finding_ids BIGINT[] NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS kb_deliveries (
    digest_id BIGINT NOT NULL REFERENCES kb_digests(id), chat_id BIGINT NOT NULL,
    sent_at TIMESTAMPTZ, attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
    retry_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(digest_id, chat_id)
);
CREATE TABLE IF NOT EXISTS kb_generated (
    id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL, question TEXT NOT NULL, answer JSONB NOT NULL,
    chunk_ids BIGINT[] NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS kb_state (key TEXT PRIMARY KEY, value JSONB NOT NULL);
