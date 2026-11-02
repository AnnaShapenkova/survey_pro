from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import db
from kb import analysis, llm
from kb.answers import answer
from kb.digests import create_digest, deliver
from kb.ingest import bootstrap
from kb.search import index_batch, search
from kb.store import save_document
from worker.main import process_one


TEXT = "SurveyJS supports conditional visibility. Use visibleIf for question logic.\n{\"visibleIf\": \"{age} >= 18\"}"


def save(conn, url="https://example.com/logic", text=TEXT):
    return save_document(conn, url=url, title="Conditional visibility", platform="SurveyJS",
                         source_type="official", raw=text.encode(), mime="text/plain")


def model_response(conn, version):
    chunk = conn.execute("SELECT id FROM kb_chunks WHERE version_id=%s LIMIT 1", (version,)).fetchone()["id"]
    return dict(relevant=True, summary="Условный показ вопросов", platforms=["SurveyJS"], topics=["logic"],
                task="Показать вопрос по условию", solution="Использовать visibleIf", limitations="Проверьте версию",
                original_code='{\"visibleIf\": \"{age} >= 18\"}', is_new_knowledge=True,
                evidence=[{"chunk_id": chunk, "quote": "SurveyJS supports conditional visibility."}])


def test_document_dedup_versions_and_latest_search(conn):
    v1, created = save(conn)
    assert created
    assert save(conn)[1] is False
    assert save(conn, "https://example.org/reprint")[0] == v1
    v2, created = save(conn, text=TEXT + "\nUPDATED")
    assert created and v2 != v1
    assert conn.execute("SELECT count(*) AS n FROM kb_versions").fetchone()["n"] == 2
    assert search(conn, "UPDATED")[0]["version_id"] == v2
    assert search(conn, "visibleIf")
    # A reverted document reuses the old content but must become current again.
    assert save(conn)[0] == v1
    assert search(conn, "UPDATED") == []


def test_queue_analysis_digest_and_recipient_retry(conn):
    version, _ = save(conn)
    with patch.object(llm, "chat", return_value=model_response(conn, version)):
        assert process_one(conn)
    assert conn.execute("SELECT status FROM kb_versions WHERE id=%s", (version,)).fetchone()["status"] == "done"
    for chat in (1, 2):
        conn.execute("INSERT INTO kb_subscribers(chat_id) VALUES (%s)", (chat,))
    now = datetime(2026, 11, 2, 10, tzinfo=ZoneInfo("Europe/Moscow"))
    assert create_digest(conn, now)
    assert create_digest(conn, now) is None
    sent = []
    def send(chat_id, text):
        if chat_id == 2:
            raise RuntimeError("recipient unavailable")
        sent.append(chat_id)
    deliver(conn, send)
    assert sent == [1]
    assert conn.execute("SELECT sent_at FROM kb_deliveries WHERE chat_id=1").fetchone()["sent_at"]
    assert conn.execute("SELECT sent_at FROM kb_deliveries WHERE chat_id=2").fetchone()["sent_at"] is None
    deliver(conn, send)
    assert sent == [1]  # successful recipient is not retried


def test_worker_errors_are_retryable_and_bounded(conn):
    version, _ = save(conn)
    with patch.object(llm, "chat", side_effect=ValueError("bad output")):
        process_one(conn)
    row = conn.execute("SELECT status,attempts FROM kb_versions WHERE id=%s", (version,)).fetchone()
    assert row == {"status": "failed", "attempts": 1}
    assert analysis.claim(conn) is None
    conn.execute("UPDATE kb_versions SET attempts=3,retry_at=now()-interval '1 minute'")
    assert analysis.claim(conn) is None


def test_real_pdf_import_has_page_locators_and_is_idempotent(conn):
    bootstrap(conn)
    bootstrap(conn)
    assert conn.execute("SELECT count(*) AS n FROM kb_documents").fetchone()["n"] == 1
    found = search(conn, "вопросы", reference=True)
    assert found and all("стр. PDF" in r["locator"] for r in found)
    assert conn.execute("SELECT count(*) AS n FROM kb_chunks").fetchone()["n"] > 140


def test_adaptation_requires_manual_citation_and_is_archived(conn):
    bootstrap(conn)
    save(conn)
    docs = search(conn, "visibleIf", reference=False)
    refs = search(conn, "Single", reference=True)
    with patch("kb.answers.search", side_effect=[docs, refs]), patch.object(llm, "chat", return_value={
        "answer": "Подход требует проверки", "code": "", "limitations": "Недостаточно синтаксиса", "citations": [refs[0]["id"]]}):
        text = answer(conn, "Как реализовать условие?", adapt=True)
    assert "стр. PDF" in text
    assert conn.execute("SELECT answer FROM kb_generated").fetchone()["answer"]["tested"] is False


def test_embedding_index_and_semantic_retrieval(conn, monkeypatch):
    save(conn)
    monkeypatch.setenv("EMBEDDING_MODEL", "test/embedding")
    with patch.object(llm, "embed", side_effect=lambda texts: [[1.0, 0.0] for _ in texts]):
        assert index_batch(conn) == 1
        found = search(conn, "показать по условию")
    assert found[0]["platform"] == "SurveyJS"


def test_web_escapes_sources(conn):
    from web.main import render
    save(conn, text=TEXT + " <script>alert('x')</script>")
    page = render(conn, "visibleIf")
    assert "&lt;script&gt;" in page and "<script>" not in page


def test_web_http_health_and_auth(conn, monkeypatch):
    import base64
    import threading
    import requests
    from http.server import ThreadingHTTPServer
    from web.main import Handler
    save(conn)
    monkeypatch.setenv("WEB_USER", "research")
    monkeypatch.setenv("WEB_PASSWORD", "test-password")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert requests.get(base + "/health", timeout=5).status_code == 200
        assert requests.get(base, timeout=5).status_code == 401
        page = requests.get(base + "/?q=visibleIf", auth=("research", "test-password"), timeout=5)
        assert page.status_code == 200 and "visibleIf" in page.text
        assert requests.get(base + "/original?id=1", timeout=5).status_code == 401
        original = requests.get(base + "/original?id=1", auth=("research", "test-password"), timeout=5)
        assert original.status_code == 200 and original.content == TEXT.encode()
        assert "attachment" in original.headers["Content-Disposition"]
    finally:
        server.shutdown()
        server.server_close()


def test_bot_subscription_and_commands_without_real_telegram(conn, monkeypatch):
    from bot.main import handle
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "100")
    calls = []
    with patch("bot.main.say", side_effect=lambda chat, text: calls.append((chat, text))):
        handle(conn, {"chat": {"id": 100, "type": "private"}, "text": "/start"})
        assert conn.execute("SELECT count(*) AS n FROM kb_subscribers").fetchone()["n"] == 1
        handle(conn, {"chat": {"id": 100, "type": "private"}, "text": "/status"})
        handle(conn, {"chat": {"id": 200, "type": "private"}, "text": "/start"})
        assert conn.execute("SELECT count(*) AS n FROM kb_subscribers").fetchone()["n"] == 1
        handle(conn, {"chat": {"id": 100, "type": "private"}, "text": "/stop"})
        assert conn.execute("SELECT count(*) AS n FROM kb_subscribers").fetchone()["n"] == 0
    assert len(calls) == 4


def test_revoked_subscriber_stops_receiving_digests(conn, monkeypatch):
    from bot.main import sync_subscribers
    for chat in (1, 2):
        conn.execute("INSERT INTO kb_subscribers(chat_id) VALUES (%s)", (chat,))
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1")
    sync_subscribers(conn)
    assert conn.execute("SELECT chat_id FROM kb_subscribers").fetchall() == [{"chat_id": 1}]
