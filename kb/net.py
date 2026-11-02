"""Bounded external fetching. Never fetch private network addresses from sources."""
import ipaddress
import os
import socket
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def public_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Expected public HTTP(S) URL without credentials")
    for info in socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)):
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError("Private network URL is not allowed")
    return url


def fetch(url, *, github=False, limit=12_000_000):
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=Retry(total=2, backoff_factor=1, status_forcelist=[429, 502, 503, 504], respect_retry_after_header=False)))
    headers = {"User-Agent": "SurveyResearch/1.0", "Accept": "*/*"}
    token = os.getenv("GITHUB_TOKEN", "")
    for _ in range(5):
        public_url(url)
        auth = {"Authorization": f"Bearer {token}"} if github and token and urlsplit(url).hostname == "api.github.com" else {}
        with session.get(url, headers=headers | auth, timeout=(10, 40), stream=True, allow_redirects=False) as response:
            if response.is_redirect:
                from urllib.parse import urljoin
                url = urljoin(url, response.headers["Location"])
                continue
            response.raise_for_status()
            data = bytearray()
            for block in response.iter_content(65536):
                data.extend(block)
                if len(data) > limit:
                    raise ValueError("Document exceeds download limit")
            return bytes(data), response.headers.get("Content-Type", "text/plain"), url
    raise ValueError("Too many redirects")
