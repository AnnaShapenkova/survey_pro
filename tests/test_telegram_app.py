import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from unittest.mock import patch

import pytest

from kb.telegram_auth import mini_app_url, validate_init_data

TOKEN = "unit-test-bot-token"


def signed_data(user_id=100, timestamp=None, token=TOKEN, **extra):
    fields = {"auth_date": str(int(time.time()) if timestamp is None else timestamp),
              "user": json.dumps({"id": user_id, "first_name": "Tester"}, separators=(",", ":")),
              "query_id": "test-query", **extra}
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    text = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, text.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture
def telegram_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "100")
    monkeypatch.setenv("MINI_APP_URL", "https://example.com")


def test_telegram_signature_accepts_signed_launch_only(telegram_env):
    assert validate_init_data(signed_data())["id"] == 100
    with pytest.raises(ValueError):
        validate_init_data(signed_data(token="different-token"))
    with pytest.raises(ValueError):
        validate_init_data(signed_data().replace("100", "101"))
    with pytest.raises(ValueError):
        validate_init_data("user=%7B%22id%22%3A100%7D")


def test_telegram_launch_rejects_expired_future_duplicate_and_unallowed(telegram_env):
    with pytest.raises(ValueError):
        validate_init_data(signed_data(timestamp=100), now=3701)
    with pytest.raises(ValueError):
        validate_init_data(signed_data(timestamp=200), now=100)
    with pytest.raises(ValueError):
        validate_init_data(signed_data() + "&auth_date=123")
    with pytest.raises(PermissionError):
        validate_init_data(signed_data(user_id=200))


@pytest.mark.parametrize("url", ["http://example.com", "https://user:password@example.com", "https://example.com/?token=abc", "https://example.com/other"])
def test_miniapp_url_rejects_wrong_or_credentialled_address(monkeypatch, url):
    monkeypatch.setenv("MINI_APP_URL", url)
    with pytest.raises(ValueError):
        mini_app_url()


def test_bot_registers_new_menu_and_button(telegram_env):
    from bot.main import configure_menu, say
    with patch("bot.main.api") as api:
        configure_menu()
        assert api.call_args_list[-1].args == ("setChatMenuButton", {"menu_button": {
            "type": "web_app", "text": "Лента", "web_app": {"url": "https://example.com/telegram"}}})
        say(100, "Открыть библиотеку")
        button = api.call_args.args[1]["reply_markup"]["inline_keyboard"][0][0]
        assert button["web_app"]["url"] == "https://example.com/telegram"
        say(200, "Доступ не разрешён")
        assert "reply_markup" not in api.call_args.args[1]


def test_menu_resets_stale_link_without_url(monkeypatch):
    from bot.main import configure_menu
    monkeypatch.delenv("MINI_APP_URL", raising=False)
    with patch("bot.main.api") as api:
        configure_menu()
        assert api.call_args.args == ("setChatMenuButton", {"menu_button": {"type": "commands"}})


def test_miniapp_http_auth_search_download_and_revocation(conn, telegram_env, monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    import requests
    from kb.store import save_document
    from web.main import Handler
    raw = b'Technical documentation explains conditional visibility and validation of survey questions.'
    version, _ = save_document(conn, url="https://example.com/article", title="Article", platform="SurveyJS",
        source_type="official", raw=raw, mime="text/plain")
    monkeypatch.setenv("WEB_PASSWORD", "browser-password")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        shell = requests.get(base + "/telegram", timeout=5)
        assert shell.status_code == 200 and "telegram-web-app.js" in shell.text
        assert "conditional visibility" not in shell.text
        assert "frame-ancestors https://web.telegram.org" in shell.headers["Content-Security-Policy"]
        assert requests.get(base + "/telegram/app.js", timeout=5).status_code == 200
        assert requests.get(base, timeout=5).status_code == 401
        assert requests.post(base + "/telegram/feed", json={}, timeout=5).status_code == 401
        assert requests.post(base + "/telegram/feed", json={"init_data": signed_data(user_id=200)}, timeout=5).status_code == 403
        valid = {"init_data": signed_data()}
        response = requests.post(base + "/telegram/feed", json=valid, timeout=5)
        assert response.status_code == 200 and "документов" in response.text
        assert "{{CARDS}}" not in response.text
        result = requests.post(base + "/telegram/feed", json=valid | {"query": "visibility"}, timeout=5)
        assert result.status_code == 200 and "conditional visibility" in result.text
        original = requests.post(base + "/telegram/original", json=valid | {"version_id": version}, timeout=5)
        assert original.status_code == 200 and original.content == raw
        assert requests.get(base + f"/original?id={version}", timeout=5).status_code == 401
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
        assert requests.post(base + "/telegram/feed", json=valid, timeout=5).status_code == 403
    finally:
        server.shutdown()
        server.server_close()
