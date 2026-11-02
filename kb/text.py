import hashlib
import io
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from pypdf import PdfReader


def canonical_url(url):
    p = urlsplit(url)
    if p.scheme not in {"http", "https", "file"}:
        raise ValueError("Unsupported URL")
    query = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"})
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(query), ""))


def normalized(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def content_hash(text):
    # Preserve indentation: whitespace can change the meaning of source code.
    stable = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip("\n")
    return hashlib.sha256(unicodedata.normalize("NFC", stable).encode("utf-8")).hexdigest()


def extract(raw, mime, title=""):
    """Return text and page-aware sections. Code line breaks are retained."""
    if "pdf" in mime or raw.startswith(b"%PDF"):
        pages = [(f"стр. PDF {i}", page.extract_text() or "") for i, page in enumerate(PdfReader(io.BytesIO(raw)).pages, 1)]
        return "\n\n".join(t for _, t in pages), pages
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = raw.decode("cp1251", errors="replace")
    if "html" in mime:
        soup = BeautifulSoup(decoded, "html.parser")
        for node in soup.select("script,style,nav,header,footer,aside,noscript,svg,form"):
            node.decompose()
        root = soup.select_one("article") or soup.select_one("main") or soup.body or soup
        for node in root.select("pre"):
            node.replace_with("\n```\n" + node.get_text("", strip=False) + "\n```\n")
        decoded = root.get_text("\n", strip=True)
    return decoded, [(title or "документ", decoded)]


def chunks(sections, size=2400, overlap=250):
    for locator, text in sections:
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):
                boundary = text.rfind("\n", start + size // 2, end)
                if boundary > start:
                    end = boundary
            part = text[start:end].strip()
            if part:
                yield locator, part
            if end == len(text):
                break
            start = max(start + 1, end - overlap)
