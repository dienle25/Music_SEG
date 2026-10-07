# =========================================================
# url_filter.py - chuẩn hóa và lọc URL HopAmChuan
# =========================================================

import re
from urllib.parse import urljoin, urldefrag, urlparse, urlunparse
import config


def normalize_url(current_url, href):
    if not href:
        return None
    href = href.strip()
    if not href or href.startswith("#"):
        return None
    if href.lower().startswith(("javascript:", "mailto:", "tel:", "sms:", "data:")):
        return None

    absolute, _ = urldefrag(urljoin(current_url, href))
    p = urlparse(absolute)
    if p.scheme not in ("http", "https"):
        return None

    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host != config.DOMAIN:
        return None

    path = re.sub(r"/{2,}", "/", p.path or "/")
    if len(path) > 1:
        path = path.rstrip("/") + "/"

    # Giữ query offset trên trang rhythm; bỏ query ở song URL.
    query = p.query if path.startswith("/rhythm/") else ""
    return urlunparse(("https", config.DOMAIN, path, "", query, ""))


def is_song_url(url):
    path = urlparse(url).path
    return any(re.match(pattern, path, re.I) for pattern in config.SONG_URL_PATTERNS)


def is_discovery_url(url):
    p = urlparse(url)
    return any(re.match(pattern, p.path, re.I) for pattern in config.DISCOVERY_PATTERNS)


def check_url(url, depth):
    p = urlparse(url)
    if p.scheme != "https":
        return False, "INVALID_SCHEME"
    if p.netloc != config.DOMAIN:
        return False, "OUTSIDE_DOMAIN"
    if depth > config.MAX_DEPTH:
        return False, "MAX_DEPTH"
    if p.path.lower().endswith(config.BLOCKED_EXTENSIONS):
        return False, "NON_HTML_RESOURCE"
    if is_song_url(url):
        return True, "SONG"
    if is_discovery_url(url):
        return True, "DISCOVERY"
    return False, "NOT_FOCUSED_PAGE"
