import base64
import hmac
import html
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import db
from kb.config import data_dir, integer
from kb.search import search


def e(value):
    return html.escape(str(value), quote=True)


def render(conn, query=""):
    if query:
        rows = search(conn, query, limit=18)
        cards = "".join(f'<article><div class="meta">{e(r["platform"])} / {e(r["source_type"])} · {e(r["locator"])}</div><h2>{e(r["title"])}</h2><p class="excerpt">{e(r["text"])}</p>' +
            (f'<a href="{e(r["url"])}" target="_blank" rel="noopener noreferrer">Первоисточник ↗</a>' if r["url"].startswith("http") else '<span class="meta">Загруженный документ</span>') + '</article>' for r in rows)
    else:
        rows = conn.execute("""SELECT f.id,f.summary,f.analysis,f.version_id,d.title,d.url,d.platform,d.source_type
            FROM kb_findings f JOIN LATERAL(SELECT doc.* FROM kb_documents doc JOIN kb_document_versions dv
            ON dv.document_id=doc.id WHERE dv.version_id=f.version_id ORDER BY doc.id LIMIT 1)d ON true
            ORDER BY f.id DESC LIMIT 40""").fetchall()
        cards = ""
        for row in rows:
            a = row["analysis"]
            cards += f'<article><div class="meta">{e(row["platform"])} / {e(row["source_type"])}</div><h2>{e(row["summary"])}</h2><p><b>Задача.</b> {e(a.get("task",""))}</p><p>{e(a.get("solution",""))}</p>'
            if a.get("limitations"):
                cards += f'<p class="meta">Ограничения: {e(a["limitations"])}</p>'
            if a.get("coverage", {}).get("partial"):
                cards += '<p class="meta">Проанализирована часть документа.</p>'
            cards += '<details><summary>Доказательства и код</summary>'
            for evidence in a.get("evidence", []):
                cards += f'<blockquote>{e(evidence["quote"])}</blockquote>'
            if a.get("original_code"):
                cards += f'<p class="meta">Код из источника; выполнение не проверено</p><pre>{e(a["original_code"])}</pre>'
            cards += '</details>'
            if row["url"].startswith("http"):
                cards += f'<a href="{e(row["url"])}" target="_blank" rel="noopener noreferrer">Первоисточник ↗</a> · '
            cards += f'<a href="/original?id={row["version_id"]}">Сохранённый оригинал ↓</a></article>'
    counts = conn.execute("SELECT (SELECT count(*) FROM kb_documents) AS docs,(SELECT count(*) FROM kb_findings) AS findings").fetchone()
    template = Path(__file__).with_name("template.html").read_text(encoding="utf-8")
    return template.replace("{{QUERY}}", e(query)).replace("{{DOCS}}", str(counts["docs"])).replace("{{FINDINGS}}", str(counts["findings"])).replace("{{CARDS}}", cards or '<div class="empty">Пока ничего не найдено.<br>Материалы появятся после сбора и анализа. Состояние доступно в Telegram: /status.</div>')


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path)
        try:
            if path.path == "/health":
                with db.connect() as conn:
                    conn.execute("SELECT 1")
                self.respond(200, b"ok", "text/plain")
                return
            password = os.getenv("WEB_PASSWORD", "")
            expected = "Basic " + base64.b64encode((os.getenv("WEB_USER", "research") + ":" + password).encode()).decode()
            if not password or not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Survey library", charset="UTF-8"')
                self.end_headers()
                self.wfile.write(b"Set WEB_PASSWORD and sign in to open the library.")
                return
            query = parse_qs(path.query)
            with db.connect() as conn:
                if path.path == "/":
                    self.respond(200, render(conn, query.get("q", [""])[0][:1000]).encode(), "text/html; charset=utf-8")
                elif path.path == "/original":
                    version = int(query.get("id", ["0"])[0])
                    row = conn.execute("SELECT raw_path FROM kb_versions WHERE id=%s", (version,)).fetchone()
                    if not row:
                        self.respond(404, b"not found", "text/plain")
                        return
                    file = (data_dir() / row["raw_path"]).resolve()
                    if not file.is_relative_to(data_dir().resolve()):
                        raise ValueError("Invalid archive path")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", f'attachment; filename="source-{version}.bin"')
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(file.read_bytes())
                else:
                    self.respond(404, b"not found", "text/plain")
        except Exception as error:
            db.log("web", type(error).__name__)
            self.respond(503, b"service unavailable", "text/plain")

    def respond(self, status, body, kind):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve():
    ThreadingHTTPServer(("0.0.0.0", integer("PORT", 8080)), Handler).serve_forever()


def main():
    with db.connect() as conn:
        db.init_schema(conn)
    serve()
