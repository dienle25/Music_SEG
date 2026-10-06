"""Trích xuất bản ghi trang và hyperlink từ HTML bằng BeautifulSoup."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from urls import normalize_url


def parse_page(html: str, url: str, depth: int, status_code: int = 200) -> dict:
    """Extract assignment fields without tokenizing or preprocessing the text."""
    soup = BeautifulSoup(html, "html.parser")
    title = " ".join(soup.title.get_text(" ", strip=True).split()) if soup.title else ""
    for element in soup.find_all(["script", "style", "noscript", "template", "svg", "head"]):
        element.decompose()
    # Explicitly hidden elements do not contribute to visible page content.
    for element in list(soup.select('[hidden], [aria-hidden="true"]')):
        if element.parent is not None:
            element.decompose()
    content_root = soup.body or soup
    content = " ".join(content_root.get_text(" ", strip=True).split())
    normalized = normalize_url(url)
    if normalized is None:
        raise ValueError(f"Invalid page URL: {url!r}")
    return {
        "url": normalized,
        "domain": urlsplit(normalized).hostname or "",
        "title": title,
        "content": content,
        "depth": depth,
        "status_code": status_code,
        "crawled_at": datetime.now(timezone.utc).isoformat(),
    }


def extract_links(html: str, base_url: str) -> list[str]:
    """Return normalized unique hyperlinks in document order, before domain filtering."""
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    seen: set[str] = set()
    for element in soup.find_all("a", href=True):
        normalized = normalize_url(element.get("href"), base_url)
        if normalized is not None and normalized not in seen:
            seen.add(normalized)
            links.append(normalized)
    return links
