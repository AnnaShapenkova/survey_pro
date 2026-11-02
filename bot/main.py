"""Existing-token Telegram integration: polling, library questions and daily digests."""
import mimetypes
import os
import threading
import time
from pathlib import Path

import requests

import db
from kb.answers import answer
from kb.config import integer
from kb.digests import create_digest, deliver
from kb.search import search
from kb.store import save_document

COMMANDS = [("start", "Подписаться на дайджест"), ("stop", "Отписаться"),
            ("status", "Состояние базы и сбора"), ("search", "Поиск по библиотеке"),
            ("ask", "Вопрос по материалам"), ("adapt", "Адаптация для EnjoySurvey 4.1"), ("id", "ID вашего чата")]


def api(method, payload=None, timeout=45):
    token = os.environ["TELEGRAM_TOKEN"]
    response = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload or {}, timeout=(10, timeout))
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {body.get('error_code')}: {body.get('description','API error')}")
    return body["result"]


def message_parts(text, limit=3500):
    # Telegram's limit uses UTF-16 units. Plain text cannot split HTML entities.
    part, size = [], 0
    for char in text:
        units = len(char.encode("utf-16-le")) // 2
        if size + units > limit:
            yield "".join(part)
            part, size = [], 0
        part.append(char)
        size += units
    if part:
        yield "".join(part)


def say(chat_id, text):
    for part in message_parts(text):
        api("sendMessage", {"chat_id": chat_id, "text": part, "disable_web_page_preview": True})


def allowed(chat_id):
    ids = {x.strip() for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if x.strip()}
    return str(chat_id) in ids


def sync_subscribers(conn):
    ids = [int(x.strip()) for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if x.strip()]
    conn.execute("DELETE FROM kb_subscribers WHERE NOT(chat_id=ANY(%s))", (ids,))


def status_text(conn):
    counts = conn.execute("""SELECT (SELECT count(*) FROM kb_documents) AS documents,
        (SELECT count(*) FROM kb_findings) AS findings,
        (SELECT count(*) FROM kb_versions WHERE status='failed') AS failed,
        (SELECT count(*) FROM kb_chunks WHERE embedding IS NOT NULL) AS indexed""").fetchone()
    runs = conn.execute("SELECT DISTINCT ON(service) service,status,started_at,items,note FROM kb_runs ORDER BY service,id DESC").fetchall()
    text = f"Документов: {counts['documents']}; решений: {counts['findings']}; ошибок анализа: {counts['failed']}.\nФрагментов с поисковым индексом по смыслу: {counts['indexed']}."
    for r in runs:
        text += f"\n{r['service']}: {r['status']}, {r['started_at']:%d.%m %H:%M UTC}, новых: {r['items']}"
    errors = conn.execute("SELECT key,error FROM kb_sources WHERE active AND error IS NOT NULL").fetchall()
    if errors:
        text += "\nИсточники с ошибками: " + ", ".join(r["key"] for r in errors)
    return text


def receive_document(conn, document):
    name = Path(document.get("file_name", "upload.txt")).name
    if Path(name).suffix.lower() not in {".pdf", ".txt", ".md", ".json", ".js", ".php", ".css", ".xml", ".lss", ".csv", ".yaml", ".yml"}:
        return "Поддерживаются PDF, текстовые анкеты и файлы скриптов. Word сначала сохраните в PDF."
    if document.get("file_size", 0) > 20_000_000:
        return "Максимальный размер файла — 20 МБ."
    info = api("getFile", {"file_id": document["file_id"]})
    path = info["file_path"]
    # This path is supplied by Telegram, never by the uploaded document.
    if ".." in path or path.startswith("/") or ":" in path:
        raise ValueError("Invalid Telegram file path")
    url = f"https://api.telegram.org/file/bot{os.environ['TELEGRAM_TOKEN']}/{path}"
    raw = bytearray()
    with requests.get(url, timeout=(10, 60), stream=True) as response:
        if response.status_code != 200:
            raise RuntimeError("Telegram file download failed")
        for block in response.iter_content(65536):
            raw.extend(block)
            if len(raw) > 20_000_000:
                raise ValueError("File exceeds 20 MB")
    version, created = save_document(conn, url="file:///uploads/" + document["file_unique_id"], title=name,
        platform="EnjoySurvey", source_type="manual", raw=bytes(raw), mime=mimetypes.guess_type(name)[0] or "text/plain")
    return f"Файл сохранён в общей библиотеке. Версия {version}. " + ("Добавлен в очередь анализа." if created else "Этот материал уже есть в базе.")


def handle(conn, message):
    chat = message["chat"]
    if chat.get("type") != "private":
        return
    text = (message.get("text") or "").strip()
    command, _, question = text.partition(" ")
    command = command.split("@")[0]
    if command == "/id":
        say(chat["id"], f"ID чата: {chat['id']}")
        return
    if not allowed(chat["id"]):
        say(chat["id"], "Доступ к библиотеке ещё не открыт. Узнайте /id и передайте его владельцу для TELEGRAM_ALLOWED_CHAT_IDS.")
        return
    if "document" in message:
        say(chat["id"], receive_document(conn, message["document"]))
    elif command == "/start":
        conn.execute("INSERT INTO kb_subscribers(chat_id,name) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                     (chat["id"], chat.get("username") or chat.get("first_name") or ""))
        say(chat["id"], "Подписка включена. Ежедневно пришлю дайджест полезных обновлений.\n/search запрос — поиск\n/ask вопрос — ответ по источникам\n/adapt задача — адаптация для EnjoySurvey 4.1\nМожно прислать PDF, анкету или скрипт для общей библиотеки.\n/stop — отписаться.")
    elif command == "/stop":
        conn.execute("DELETE FROM kb_subscribers WHERE chat_id=%s", (chat["id"],))
        say(chat["id"], "Подписка отключена.")
    elif command == "/status":
        say(chat["id"], status_text(conn))
    elif command in {"/ask", "/adapt"} and question:
        say(chat["id"], answer(conn, question, adapt=command == "/adapt"))
    elif command == "/search" and question:
        rows = search(conn, question, limit=6)
        say(chat["id"], "\n\n".join(f"{r['platform']} · {r['title']} · {r['locator']}\n{r['text'][:600]}\n" +
            (r["url"] if r["url"].startswith("http") else "Локальный документ") for r in rows) or "Материалов не найдено.")
    elif text and not text.startswith("/"):
        say(chat["id"], answer(conn, text))
    else:
        say(chat["id"], "Команды: /start, /stop, /status, /search запрос, /ask вопрос, /adapt задача, /id.")


def polling():
    with db.connect() as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(4210) AS locked").fetchone()["locked"]:
            raise RuntimeError("Another bot process is already running")
        api("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in COMMANDS]})
        if api("getWebhookInfo").get("url"):
            raise RuntimeError("Existing webhook is enabled; stop old bot and remove webhook before enabling polling")
        me = api("getMe")
        db.log("bot", f"Connected: @{me.get('username', 'bot')}")
        while True:
            try:
                state = conn.execute("SELECT value FROM kb_state WHERE key='telegram_offset'").fetchone()
                offset = state["value"] if state else 0
                updates = api("getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": ["message"]}, timeout=35)
                for update in updates:
                    try:
                        if "message" in update:
                            handle(conn, update["message"])
                    except Exception as error:
                        db.log("bot", f"Message failed: {type(error).__name__}")
                        if "message" in update:
                            say(update["message"]["chat"]["id"], "Не удалось обработать запрос. Проверьте /status и настройки модели; попробуйте ещё раз.")
                    conn.execute("INSERT INTO kb_state(key,value) VALUES ('telegram_offset',%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (db.Jsonb(update["update_id"] + 1),))
                sync_subscribers(conn)
                create_digest(conn)
                deliver(conn, say)
            except Exception as error:
                db.log("bot", f"Polling/delivery error: {type(error).__name__}")
                time.sleep(10)


def main():
    with db.connect() as conn:
        db.init_schema(conn)
    # Web server lives in the bot container, as in upstream's Mini App deployment.
    from web.main import serve
    threading.Thread(target=serve, daemon=True).start()
    if not os.getenv("TELEGRAM_TOKEN"):
        raise SystemExit("Set TELEGRAM_TOKEN in Coolify")
    polling()
