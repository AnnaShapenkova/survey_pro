import json
from unittest.mock import patch

from collector.main import candidates, collect
from kb.scheduler import tick


def test_tags_skip_nonversion_names_without_losing_limit():
    tags = [{"name": name, "commit": {"sha": "abc"}} for name in ("remove", "list", "7.8.0+261026", "7.7.0+261020")]
    with patch("collector.main.fetch", return_value=(json.dumps(tags).encode(), "application/json", "https://api.github.com")):
        found = list(candidates({"kind": "github_tags", "repo": "LimeSurvey/LimeSurvey", "limit": 1}))
    assert len(found) == 1 and "7.8.0" in found[0]["title"]


def test_listing_ignores_blog_categories():
    page = b'<a href="/blog/product-news">Category</a><a href="/blog/product-news/new-version">Actual article</a>'
    calls = []
    def fetch(url):
        calls.append(url)
        return (page if len(calls) == 1 else b'An actual article with technical details of survey logic.'), "text/html", url
    with patch("collector.main.fetch", side_effect=fetch):
        found = list(candidates({"kind": "listing", "url": "https://example.com/blog", "selector": "a",
            "exclude_pattern": "^/blog/[^/]+/?$", "limit": 1}))
    assert len(found) == 1 and calls[-1].endswith("new-version")


def test_collection_isolates_source_failure_and_scheduler_persists(conn, tmp_path, monkeypatch):
    configs = [{"key": key, "kind": "page", "name": key, "url": "https://example.com/" + key,
                "platform": "SurveyJS", "source_type": "official"} for key in ("broken", "working")]
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(configs))
    monkeypatch.setenv("SOURCES_FILE", str(path))
    def reader(config):
        if config["key"] == "broken":
            raise ValueError("broken source")
        yield {"url": config["url"], "title": "Task", "raw": b'Technical article explaining conditional survey question visibility.', "mime": "text/plain"}
    with patch("collector.main.candidates", side_effect=reader):
        assert collect(conn) == 1
        assert collect(conn) == 0
    assert conn.execute("SELECT error FROM kb_sources WHERE key='broken'").fetchone()["error"]
    with patch("collector.main.collect") as collection:
        tick(conn)
        collection.assert_not_called()
