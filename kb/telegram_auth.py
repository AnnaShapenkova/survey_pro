"""Verify Telegram Mini App launch data before returning any library content."""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl, urlsplit, urlunsplit


def allowed(user_id):
    ids = {x.strip() for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if x.strip()}
    return str(user_id) in ids


def mini_app_url():
    value = os.getenv("MINI_APP_URL", "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("MINI_APP_URL must be a public HTTPS address without credentials or query parameters")
    path = parts.path.rstrip("/")
    if path not in {"", "/telegram"}:
        raise ValueError("MINI_APP_URL must point to the site root or /telegram")
    return urlunsplit(("https", parts.netloc, "/telegram", "", ""))


def validate_init_data(init_data, *, now=None, max_age=3600):
    if not isinstance(init_data, str) or not init_data or len(init_data) > 16384:
        raise ValueError("Откройте ленту кнопкой в личном чате с ботом.")
    token = os.getenv("TELEGRAM_TOKEN", "")
    if not token:
        raise RuntimeError("Telegram authentication is not configured")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("Некорректные данные запуска Telegram.")
    supplied_hash = fields.pop("hash", "")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied_hash.encode(), expected_hash.encode()):
        raise ValueError("Подпись Telegram не прошла проверку. Закройте и снова откройте ленту.")
    try:
        timestamp = int(fields["auth_date"])
        user = json.loads(fields["user"])
        user_id = user["id"]
    except (KeyError, ValueError, TypeError):
        raise ValueError("В данных Telegram отсутствует пользователь или время запуска.") from None
    current = time.time() if now is None else now
    if current - timestamp > max_age or timestamp - current > 30:
        raise ValueError("Сессия ленты истекла. Закройте и снова откройте её из бота.")
    if type(user_id) is not int or user_id <= 0 or not allowed(user_id):
        raise PermissionError("Доступ к библиотеке не открыт для вашего Telegram ID.")
    return user
