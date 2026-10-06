"""Chuẩn hóa URL và lọc tên miền/tài nguyên trước khi đưa vào frontier."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from urllib.parse import unquote, unquote_plus, urljoin, urlsplit, urlunsplit


NON_HTML_EXTENSIONS = frozenset({
    ".7z", ".aac", ".avi", ".avif", ".bmp", ".bz2", ".css", ".csv",
    ".doc", ".docx", ".eot", ".epub", ".exe", ".flac", ".gif", ".gz",
    ".ico", ".ics", ".jpeg", ".jpg", ".js", ".json", ".m3u", ".m3u8",
    ".m4a", ".m4v", ".map", ".mkv", ".mov", ".mp3", ".mp4", ".mpeg",
    ".ogg", ".otf", ".pdf", ".png", ".ppt", ".pptx", ".rar", ".rss",
    ".svg", ".tar", ".tgz", ".tif", ".tiff", ".ts", ".ttf", ".txt",
    ".wav", ".webm", ".webp", ".woff", ".woff2", ".xls", ".xlsx",
    ".xml", ".zip",
})
TRACKING_PARAMETERS = frozenset({"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid"})
SPOTIFY_TRACKING_PARAMETERS = frozenset({"si", "dlsi", "nd"})
_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _normalize_hostname(host: str) -> str | None:
    """Normalize DNS/IDNA or IP names, without granting subdomain access."""
    host = host.rstrip(".").lower()
    if not host:
        return None
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if len(host) > 253 or not all(_DOMAIN_LABEL.fullmatch(label) for label in host.split(".")):
        return None
    return host


def normalize_url(url: str, base_url: str | None = None) -> str | None:
    """Return an absolute HTTP(S) URL, or None for invalid/unsupported input.

    Paths and nontracking query parameters keep their original meaning. A path
    with a trailing slash is not assumed equivalent to the same path without it.
    """
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url or "\\" in url or any(ord(character) < 32 or ord(character) == 127 for character in url):
        return None
    try:
        absolute = urljoin(base_url, url) if base_url else url
        parsed = urlsplit(absolute)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        host = _normalize_hostname(parsed.hostname)
        port = parsed.port  # Access validates malformed and out-of-range ports.
        if host is None or port == 0:
            return None
    except (ValueError, TypeError, UnicodeError):
        return None

    netloc = f"[{host}]" if ":" in host else host
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc += f":{port}"

    spotify = host == "spotify.com" or host.endswith(".spotify.com")
    query_parts = []
    for part in parsed.query.split("&"):
        key = unquote_plus(part.partition("=")[0]).lower()
        if key.startswith("utm_") or key in TRACKING_PARAMETERS:
            continue
        if spotify and key in SPOTIFY_TRACKING_PARAMETERS:
            continue
        query_parts.append(part)
    return urlunsplit((scheme, netloc, parsed.path or "/", "&".join(query_parts), ""))


def is_allowed_url(url: str, allowed_domains: Iterable[str]) -> bool:
    """Accept exact configured hosts and HTML-like paths; never infer subdomains.

    A response's Content-Type still needs checking because URL suffixes alone
    cannot determine whether a resource is HTML.
    """
    normalized = normalize_url(url)
    if normalized is None:
        return False
    parsed = urlsplit(normalized)
    domains = {
        normalized_host
        for domain in allowed_domains
        if isinstance(domain, str)
        and (normalized_host := _normalize_hostname(domain.strip())) is not None
    }
    if parsed.hostname not in domains:
        return False
    # Decode suffixes so /cover%2Ejpg is treated like /cover.jpg. Path parameters
    # must not conceal an otherwise recognizable resource extension.
    path = unquote(parsed.path).rstrip("/").rsplit("/", 1)[-1].split(";", 1)[0].lower()
    return not any(path.endswith(extension) for extension in NON_HTML_EXTENSIONS)
