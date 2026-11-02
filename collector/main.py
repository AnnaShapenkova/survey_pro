"""One collection pass; scheduler invokes it daily. Source errors are isolated."""
import json
import os
from urllib.parse import quote, urlencode, urljoin, urlsplit
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

import db
from kb.config import ROOT, integer
from kb.net import fetch
from kb.store import save_document
from kb.text import canonical_url


def error_label(error):
    response = getattr(error, "response", None)
    if response is not None:
        label = f"HTTP {response.status_code}"
        if response.headers.get("X-RateLimit-Remaining") == "0":
            label += " (GitHub rate limit; configure GITHUB_TOKEN)"
        return label
    return type(error).__name__


def candidates(config):
    kind = config["kind"]
    limit = min(config.get("limit", 10), integer("MAX_PER_SOURCE", 15))
    if kind.startswith("github_"):
        repo = config.get("repo", "")
        if kind == "github_search":
            query = urlencode({"q": config["query"], "sort": "updated", "per_page": limit})
            raw, _, _ = fetch("https://api.github.com/search/repositories?" + query, github=True)
            for item in json.loads(raw)["items"][:limit]:
                # Read the actual README, rather than analysing a repository title.
                readme_url = f"https://api.github.com/repos/{item['full_name']}/readme"
                try:
                    import base64
                    body = json.loads(fetch(readme_url, github=True)[0])
                    yield {"url": item["html_url"], "title": item["full_name"],
                           "raw": base64.b64decode(body["content"]), "mime": "text/markdown"}
                except Exception as error:
                    db.log("collector", f"README {item['full_name']}: {type(error).__name__}")
            return
        endpoint = "releases" if kind == "github_releases" else "tags"
        fetch_limit = 100 if kind == "github_tags" else limit
        raw, _, _ = fetch(f"https://api.github.com/repos/{repo}/{endpoint}?per_page={fetch_limit}", github=True)
        emitted = 0
        for item in json.loads(raw):
            if kind == "github_releases":
                if item.get("draft"):
                    continue
                text = item.get("body") or "No release notes provided."
                yield {"url": item["html_url"], "title": item.get("name") or item["tag_name"],
                       "raw": text.encode(), "mime": "text/markdown"}
            else:
                tag = item["name"]
                import re
                if not re.match(r"^v?\d+\.\d+", tag):
                    continue
                yield {"url": f"https://github.com/{repo}/tree/{quote(tag, safe='')}", "title": f"{repo}: {tag}",
                       "raw": f"Repository {repo} published version tag {tag}. Commit {item['commit']['sha']}. No feature details are available in this tag metadata.".encode(), "mime": "text/plain"}
            emitted += 1
            if emitted >= limit:
                break
        return
    raw, mime, final_url = fetch(config["url"])
    if kind == "page":
        yield {"url": config["url"], "title": config["name"], "raw": raw, "mime": mime}
        return
    if kind == "listing":
        soup = BeautifulSoup(raw, "html.parser")
        links = [(urljoin(final_url, a.get("href", "")), a.get_text(" ", strip=True))
                 for a in soup.select(config["selector"])]
    elif kind == "rss":
        tree = ET.fromstring(raw)
        links = [(x.findtext("link"), x.findtext("title") or "Publication") for x in tree.iter("item")]
        if not links:
            ns = "{http://www.w3.org/2005/Atom}"
            links = [(x.find(f"{ns}link").get("href"), x.findtext(f"{ns}title") or "Publication")
                     for x in tree.iter(f"{ns}entry") if x.find(f"{ns}link") is not None]
    else:
        raise ValueError(f"Unknown source kind: {kind}")
    seen = {canonical_url(config["url"])}
    count = 0
    for url, title in links:
        if not url or not title or urlsplit(url).scheme not in {"http", "https"}:
            continue
        if kind == "listing" and urlsplit(url).hostname != urlsplit(final_url).hostname:
            continue
        if config.get("exclude_pattern"):
            import re
            if re.search(config["exclude_pattern"], urlsplit(url).path):
                continue
        url = canonical_url(url)
        if url in seen:
            continue
        seen.add(url)
        try:
            body, content_type, _ = fetch(url)
            yield {"url": url, "title": title, "raw": body, "mime": content_type}
            count += 1
        except Exception as error:
            db.log("collector", f"Publication failed: {type(error).__name__}: {url}")
        if count >= limit:
            break


def collect(conn):
    if not conn.execute("SELECT pg_try_advisory_lock(4208) AS locked").fetchone()["locked"]:
        return 0
    run = db.start_run(conn, "collector")
    total, failures = 0, []
    try:
        path = os.getenv("SOURCES_FILE", str(ROOT / "sources.json"))
        configs = json.loads(open(path, encoding="utf-8").read())
        # The file is the source of truth, including disabled and removed sources.
        conn.execute("UPDATE kb_sources SET active=false")
        for config in configs:
            active = config.get("active", True)
            source = conn.execute("""INSERT INTO kb_sources(key,config,active) VALUES (%s,%s,%s)
                ON CONFLICT(key) DO UPDATE SET config=excluded.config,active=excluded.active RETURNING id""",
                (config["key"], db.Jsonb(config), active)).fetchone()["id"]
            if not active:
                continue
            try:
                new = 0
                for item in candidates(config):
                    _, created = save_document(conn, **item, platform=config["platform"],
                        source_type=config["source_type"], source_id=source, run_id=run)
                    total += created
                    new += created
                conn.execute("UPDATE kb_sources SET checked_at=now(),error=NULL WHERE id=%s", (source,))
                db.log("collector", f"{config['key']}: {new} new versions")
            except Exception as error:
                # Do not log full request errors that may include credentials.
                note = f"{config['key']}: {error_label(error)}"
                failures.append(note)
                conn.execute("UPDATE kb_sources SET checked_at=now(),error=%s WHERE id=%s", (note, source))
                db.log("collector", note)
        db.finish_run(conn, run, total, "; ".join(failures) or None)
        return total
    except Exception as error:
        db.finish_run(conn, run, total, type(error).__name__)
        raise
    finally:
        conn.execute("SELECT pg_advisory_unlock(4208)")


def main():
    with db.connect() as conn:
        db.init_schema(conn)
        collect(conn)
