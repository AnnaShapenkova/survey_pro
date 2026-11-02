-- Inspect ingestion and extraction failures.
SELECT key, checked_at, error FROM kb_sources WHERE active ORDER BY key;
SELECT id, status, attempts, error FROM kb_versions WHERE status IN ('failed','processing');
SELECT service, status, started_at, items, note FROM kb_runs ORDER BY id DESC LIMIT 30;

-- Explicitly retry a particular document version (replace 123).
-- UPDATE kb_versions SET status='new',attempts=0,retry_at=now(),lease_until=NULL WHERE id=123;

-- Reanalyse non-reference versions after changing MODEL or PROMPT_VERSION.
-- Existing findings remain in history. Same model + prompt version is idempotent.
-- UPDATE kb_versions v SET status='new',attempts=0,retry_at=now(),lease_until=NULL
-- WHERE EXISTS (SELECT 1 FROM kb_document_versions dv JOIN kb_documents d ON d.id=dv.document_id
--               WHERE dv.version_id=v.id AND NOT d.is_reference);

-- Delivery failures are per recipient.
SELECT digest_id,chat_id,attempts,sent_at,error FROM kb_deliveries ORDER BY digest_id DESC;
