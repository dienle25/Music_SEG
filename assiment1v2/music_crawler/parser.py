"""
parser.py - HTML parsing, page information and link extraction (Tasks 4, 5)
"""

from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

import config
from url_frontier import normalize_url, get_domain


# ---------------------------------------------------------------
# Task 4 - Extract page information
# ---------------------------------------------------------------
LYRIC_CONTAINER_HINTS = (
    "lyric", "lyrics", "loi-bai-hat", "loibaihat", "song-lyric",
)


def _is_lyric_container(tag):
    class_list = tag.get("class") or []
    tag_id = tag.get("id") or ""
    attr_text = (" ".join(class_list) + " " + tag_id).lower()
    return any(hint in attr_text for hint in LYRIC_CONTAINER_HINTS)


def _remove_lyric_blocks(soup):
    """Remove elements whose class/id suggests they hold full song lyrics."""
    targets = [
        tag
        for tag in soup.find_all(["div", "section", "article", "p", "pre"])
        if _is_lyric_container(tag)
    ]
    for tag in targets:
        if tag.parent is not None:  # skip if an ancestor was already removed
            tag.decompose()


def extract_lyrics(soup):
    """Return the lyrics text of a song page, or '' if not found."""
    for tag in soup.find_all(["div", "section", "article", "p", "pre"]):
        if _is_lyric_container(tag):
            text = tag.get_text(separator="\n", strip=True)
            if len(text) > 50:  # skip tiny containers
                return text
    return ""


def extract_page_info(soup, url, depth, status_code):
    """
    Build a structured page record from a parsed HTML page.
    No text preprocessing (tokenization, stopwords, ...) is done here.

    If config.STORE_LYRICS is False, lyric blocks (by class/id) are removed
    before the page text is extracted. If True, they are kept in 'content'.
    """
    # Title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.get_text(strip=True)
    elif soup.find("h1"):
        title = soup.find("h1").get_text(strip=True)

    # Remove script/style/noscript (and lyrics if disabled)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    if not config.STORE_LYRICS:
        _remove_lyric_blocks(soup)

    content = soup.get_text(separator=" ", strip=True)
    content = content[: config.MAX_CONTENT_LENGTH]

    return {
        "url": url,
        "domain": get_domain(url),
        "title": title,
        "content": content,
        "depth": depth,
        "status_code": status_code,
        "crawled_at": datetime.now().isoformat(timespec="seconds"),
    }


def extract_song_info(soup, url):
    """
    Extract structured song data (title, artist, album, genre, lyrics).
    Only for song pages (URL contains /bai-hat/). Returns None otherwise.

    NOTE: the meta tags below are guesses. Open a real song page, "View page
    source", and adjust if a field comes out empty.
    Call this BEFORE extract_page_info (which modifies the soup).
    """
    if "/bai-hat/" not in url:
        return None

    def meta(prop):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find(
            "meta", attrs={"name": prop}
        )
        return tag["content"].strip() if tag and tag.get("content") else ""

    title = meta("og:title")
    if not title and soup.title and soup.title.string:
        title = soup.title.get_text(strip=True)

    artist = ""
    # Many sites use "Song name - Artist" in the title
    if " - " in title:
        title, artist = [p.strip() for p in title.split(" - ", 1)]

    lyrics = extract_lyrics(soup) if config.STORE_LYRICS else ""

    return {
        "url": url,
        "title": title,
        "artist": artist,
        "album": meta("music:album"),
        "genre": meta("music:genre"),
        "lyrics": lyrics,
        "crawled_at": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------
# Task 5 - URL filtering
# ---------------------------------------------------------------
def is_allowed_domain(url):
    """
    Domain rule:
      host == allowed_domain  OR  host ends with '.' + allowed_domain
    So www.example.com and sub.example.com are accepted for 'example.com'.
    """
    host = urlparse(url).netloc.lower().split(":")[0]
    for domain in config.ALLOWED_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return True
    return False


def has_ignored_extension(url):
    path = urlparse(url).path.lower()
    return path.endswith(config.IGNORED_EXTENSIONS)


def is_valid_url(url):
    """Return True if the URL should be considered for crawling."""
    parsed = urlparse(url)

    # protocol must be http / https
    if parsed.scheme not in ("http", "https"):
        return False

    # must have a host
    if not parsed.netloc:
        return False

    # non-web resources (images, css, js, zip ...)
    if has_ignored_extension(url):
        return False

    # must belong to the allowed domains
    if not is_allowed_domain(url):
        return False

    return True


def extract_links(soup, current_url):
    """
    Extract all <a href> links, convert relative URLs to absolute URLs,
    and keep only valid ones. Returns a list of unique normalized URLs.
    """
    valid_links = []
    seen = set()

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith("#"):
            continue

        # ignore mailto:, javascript:, tel: ...
        lowered = href.lower()
        if lowered.startswith(tuple(s + ":" for s in config.IGNORED_SCHEMES)):
            continue

        absolute_url = urljoin(current_url, href)

        if not is_valid_url(absolute_url):
            continue

        normalized = normalize_url(absolute_url)
        if normalized not in seen:
            seen.add(normalized)
            valid_links.append(normalized)

    return valid_links
