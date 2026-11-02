import os
from unittest.mock import patch

import pytest
import yaml

from bot.main import allowed, message_parts
from kb.analysis import validate
from kb.search import cosine
from kb.text import canonical_url, chunks, content_hash, extract


def test_urls_remove_tracking_but_retain_semantic_params():
    assert canonical_url("https://EXAMPLE.com/a/?utm_source=x&id=3#fragment") == "https://example.com/a?id=3"
    assert canonical_url("https://example.com/a?id=4") != canonical_url("https://example.com/a?id=3")


def test_code_indentation_changes_document_identity():
    assert content_hash("if x:\n  action()") != content_hash("if x:\naction()")
    assert content_hash("abc\r\ndef") == content_hash("abc\ndef")


def test_html_preserves_code_and_discards_script():
    text, _ = extract(b'<nav>menu</nav><main><h1>Logic</h1><pre>if (x) {\n  run();\n}</pre><script>attack()</script></main>', "text/html")
    assert "  run();" in text
    assert "attack" not in text and "menu" not in text


def test_russian_legacy_script_encoding_is_preserved():
    text = "Вопрос анкеты: выберите вариант ответа"
    assert extract(text.encode("cp1251"), "text/plain")[0] == text


def test_chunks_do_not_drop_end_of_long_document():
    text = ("line of text\n" * 500) + "END_MARKER"
    result = list(chunks([("page 7", text)]))
    assert "END_MARKER" in result[-1][1]
    assert all(loc == "page 7" and len(t) <= 2400 for loc, t in result)


def analysis(**updates):
    data = dict(relevant=True, summary="Solution", platforms=["SurveyJS"], topics=["logic"], task="Task",
                solution="Solution", limitations="", original_code="", is_new_knowledge=True,
                evidence=[{"chunk_id": 1, "quote": "Valid source quote"}])
    return data | updates


def test_hallucinated_quotes_and_code_are_rejected():
    context = [{"id": 1, "text": "Valid source quote and source code"}]
    validate(analysis(), context)
    with pytest.raises(ValueError):
        validate(analysis(evidence=[{"chunk_id": 1, "quote": "A fabricated quotation"}]), context)
    with pytest.raises(ValueError):
        validate(analysis(original_code="invented.api()"), context)
    with pytest.raises(ValueError):
        validate(analysis(relevant="true"), context)


def test_telegram_text_splitting_and_access():
    text = "🙂" * 4000 + "русский текст"
    parts = list(message_parts(text))
    assert "".join(parts) == text
    assert all(len(p.encode("utf-16-le")) // 2 <= 3500 for p in parts)
    with patch.dict(os.environ, {"TELEGRAM_ALLOWED_CHAT_IDS": "12, 34"}):
        assert allowed(12) and not allowed(99)


def test_compose_has_persistence_scheduler_and_healthcheck():
    from pathlib import Path
    config = yaml.safe_load(Path("docker-compose.yml").read_text())
    assert set(config["services"]) == {"postgres", "scheduler", "worker", "bot"}
    assert config["services"]["bot"]["healthcheck"]
    assert config["services"]["worker"]["volumes"] == ["library:/data"]


def test_cosine_handles_zero_and_dimension_mismatch():
    assert cosine([1, 0], [1, 0]) == 1
    assert cosine([0, 0], [1, 0]) == 0
    assert cosine([1], [1, 2]) == 0
