# =========================================================
# parser.py - DOM extraction cho HopAmChuan
# =========================================================

import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup
import config
from url_filter import normalize_url, is_song_url


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(text):
    if text is None:
        return ""
    text = text.replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def clean_multiline(text):
    if not text:
        return ""
    text = text.replace("\r", "").replace("\xa0", " ")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def meta_content(soup, *names):
    wanted = {x.lower() for x in names}
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or tag.get("itemprop") or "").lower()
        if key in wanted and tag.get("content"):
            return clean_text(tag["content"])
    return ""


def extract_page_title(soup):
    return meta_content(soup, "og:title", "twitter:title") or clean_text(
        soup.title.get_text(" ", strip=True) if soup.title else ""
    )


def extract_page(url, depth, status_code, soup):
    return {
        "url": url,
        "title": extract_page_title(soup),
        "depth": depth,
        "status_code": status_code,
        "crawled_at": now_iso(),
    }


def extract_links(soup, current_url):
    links = set()
    for a in soup.find_all("a", href=True):
        u = normalize_url(current_url, a["href"])
        if u:
            links.add(u)
    return links


def _text_lines(node):
    return clean_multiline(node.get_text("\n", strip=True))


def _is_likely_song_content(text):
    if len(text) < config.MIN_LYRICS_CHARS:
        return False
    # Hợp âm thường được đặt trong [C], [Am], [G/B]...
    chord_hits = len(re.findall(r"\[[A-G][^\]]{0,12}\]", text))
    line_count = len([x for x in text.splitlines() if x.strip()])
    return chord_hits >= 2 or line_count >= 5


def extract_lyrics(soup):
    """Lấy phần lời + hợp âm; không tự tạo nội dung."""
    selectors = [
        "#song-content", "#song-content-container",
        ".song-content", ".song-content-container",
        ".chord-content", ".chord-content-container",
        ".song-lyric", ".lyrics", "[class*='song-content']",
        "[class*='chord-content']", "[class*='lyric']",
    ]

    candidates = []
    seen = set()
    for selector in selectors:
        try:
            nodes = soup.select(selector)
        except Exception:
            nodes = []
        for node in nodes:
            text = _text_lines(node)
            if id(node) in seen:
                continue
            seen.add(id(node))
            if _is_likely_song_content(text):
                candidates.append(text)

    # Fallback: tìm ancestor quanh h1 có nhiều dòng và chord marker.
    h1 = soup.find("h1")
    if h1:
        for parent in h1.parents:
            if getattr(parent, "name", None) not in {"div", "section", "main"}:
                continue
            text = _text_lines(parent)
            if len(text) > 100 and _is_likely_song_content(text):
                candidates.append(text)
                if len(text) > 2000:
                    break

    # Loại phần menu/phiên bản nếu fallback lấy quá rộng.
    if candidates:
        return max(candidates, key=lambda x: (len(re.findall(r"\[[A-G][^\]]{0,12}\]", x)), len(x)))

    return ""


def _artist_from_song_page(soup):
    # Trên trang bài hát, nghệ sĩ thường là link ngay sau H1.
    h1 = soup.find("h1")
    if h1:
        parent_text = clean_text(h1.parent.get_text(" ", strip=True)) if h1.parent else ""
        m = re.search(r"\n?(.+?)\s+Điệu\s+", parent_text, re.I)
        if m:
            candidate = clean_text(m.group(1))
            candidate = re.sub(r"^[A-G](?:#|b)?\s+", "", candidate)
            if candidate and candidate.lower() != clean_text(h1.get_text()).lower():
                return candidate

        # Ưu tiên các link artist/ca sĩ gần h1.
        for a in h1.parent.find_all("a", href=True) if h1.parent else []:
            txt = clean_text(a.get_text(" ", strip=True))
            href = a.get("href", "")
            if txt and ("/artist" in href or txt.lower() not in {"chọn phiên bản", "hợp âm cơ bản"}):
                return txt

    # Fallback theo nhãn.
    body = soup.get_text("\n", strip=True)
    for line in body.splitlines():
        line = clean_text(line)
        m = re.match(r"^(?:ca sĩ|nghệ sĩ|trình bày)\s*[:：]\s*(.+)$", line, re.I)
        if m:
            return clean_text(m.group(1))
    return ""


def _title_from_song_page(soup):
    h1 = soup.find("h1")
    if h1:
        return clean_text(h1.get_text(" ", strip=True))
    title = meta_content(soup, "og:title", "twitter:title")
    if title:
        return re.sub(r"\s*-\s*Hợp Âm Chuẩn.*$", "", title, flags=re.I).strip()
    return ""


def _genre_from_song_page(soup):
    # Trang có chữ "Điệu Ballad" gần phần title.
    text = soup.get_text(" ", strip=True)
    m = re.search(r"Điệu\s+([^\n|]+)", text, re.I)
    if m:
        value = clean_text(m.group(1))
        return value[:100]
    return ""


def _youtube_thumbnail(url):
    if not url:
        return ""
    m = re.search(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{6,})", url)
    if not m:
        return ""
    video_id = m.group(1).split("?")[0].split("&")[0]
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def extract_thumbnail(soup):
    # 1. og:image / twitter:image
    thumb = meta_content(soup, "og:image", "og:image:url", "twitter:image", "twitter:image:src")
    if thumb.startswith(("https://", "http://")):
        return thumb

    # 2. YouTube iframe thumbnail. Đây là URL ảnh đầy đủ, không phải ảnh inline của trang.
    for iframe in soup.find_all("iframe"):
        for attr in ("src", "data-src"):
            src = iframe.get(attr, "")
            thumb = _youtube_thumbnail(src)
            if thumb:
                return thumb

    # 3. Ảnh trực tiếp nếu website có ảnh đại diện bài.
    for img in soup.find_all("img"):
        for attr in ("src", "data-src", "data-original", "data-lazy-src"):
            src = img.get(attr, "")
            if src.startswith(("https://", "http://")):
                host = urlparse(src).netloc.lower()
                if "hopamchuan" in host or "ytimg.com" in host:
                    return src
    return ""


def extract_song(url, soup):
    if not is_song_url(url):
        return None

    title = _title_from_song_page(soup)
    artist = _artist_from_song_page(soup)
    genre = _genre_from_song_page(soup)
    lyrics = extract_lyrics(soup)
    thumbnail = extract_thumbnail(soup)

    bad_titles = {"", "trang chủ", "hợp âm chuẩn"}
    if title.lower() in bad_titles:
        return None
    if not title or not artist:
        return None
    if config.REQUIRE_THUMBNAIL and not thumbnail.startswith(("https://", "http://")):
        return None
    if config.REQUIRE_LYRICS and len(lyrics) < config.MIN_LYRICS_CHARS:
        return None

    return {
        "url": url,
        "title": title,
        "artist": artist,
        "genre": genre or None,
        "thumbnail_url": thumbnail,
        "lyrics": lyrics,
        "crawled_at": now_iso(),
    }
