# =========================================================
# parser.py - DOM extraction cho HopAmChuan
# =========================================================

import json
import re
import unicodedata
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
    """Read a metadata value as a single-line string (for titles/URLs)."""
    value = meta_content_raw(soup, *names)
    return clean_text(value)


def meta_content_raw(soup, *names):
    """Read metadata without collapsing whitespace; useful for multiline lyrics."""
    wanted = {x.lower() for x in names}
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or tag.get("itemprop") or "").lower()
        if key in wanted and tag.get("content"):
            return str(tag.get("content", "")).replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ").strip()
    return ""


def extract_page_title(soup):
    return meta_content(soup, "og:title", "twitter:title") or clean_text(
        soup.title.get_text(" ", strip=True) if soup.title else ""
    )


def extract_page(url, depth, status_code, soup):
    return {
        "url": url,
        "domain": urlparse(url).netloc.lower(),
        "title": extract_page_title(soup),
        "content": soup.get_text("\n", strip=True),
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



def _find_labeled_value(soup, label):
    """Find the adjacent value cell for a specific metadata label."""
    wanted = re.sub(r"[:：]\s*$", "", label).strip().casefold()
    for node in soup.find_all(string=True):
        txt = clean_text(str(node)).rstrip(":： ").casefold()
        if txt != wanted:
            continue
        row = node.parent
        for _ in range(5):
            if row is None:
                break
            if row.name in {"tr", "dl"}:
                cells = row.find_all(["td", "th", "dd", "dt"], recursive=False)
                for i, cell in enumerate(cells):
                    if clean_text(cell.get_text(" ", strip=True)).rstrip(":： ").casefold() == wanted:
                        if i + 1 < len(cells):
                            return cells[i + 1]
            row = row.parent
    return None


# Dòng thông tin đầu bài (không phải lời): Tone, Capo, Intro:, Chord:, Vòng hợp âm:
_LYRICS_HEADER_RE = re.compile(
    r"^[\s\(\[\-*•]*(?:tone|capo|intro|chords?|hợp âm|vòng hợp âm|vòng hoà âm|vòng hòa âm)\b",
    re.IGNORECASE,
)


def _is_lyrics_header_line(line):
    norm = unicodedata.normalize("NFC", line).strip()
    return bool(_LYRICS_HEADER_RE.match(norm))


def _strip_lyrics_header(lines):
    """Bỏ các dòng Tone / Capo / Intro: / Chord: / Vòng hợp âm: ở ĐẦU lời.

    Chỉ cắt ở phần đầu để không đụng tới lời bài hát phía sau.
    """
    i = 0
    while i < len(lines) and _is_lyrics_header_line(lines[i]):
        i += 1
    return lines[i:]


def _remove_lyrics_noise(text):
    text = unicodedata.normalize("NFC", text or "")
    # Remove chord annotations, including extended chords such as [A7#5].
    text = re.sub(r"\[[A-G](?:[#b])?[^\]\n]{0,18}\]", "", text)
    lines = []
    for line in clean_multiline(text).splitlines():
        low = line.casefold()
        if any(x in low for x in ("báo lỗi", "bao loi", "người đăng", "nguoi dang", "đăng bởi", "dang boi")):
            continue
        line = re.sub(r"\s{2,}", " ", line).strip(" -|")
        if line:
            lines.append(line)
    lines = _strip_lyrics_header(lines)
    return "\n".join(lines).strip()


def _lyrics_from_hopamchuan_class(soup):
    """Extract lyrics from HopAmChuan's dedicated .hopamchuan_lyric spans.

    The site places chords in sibling .hopamchuan_chord_inline elements, so
    collecting only .hopamchuan_lyric text excludes chords by construction.
    Preserve each .chord_lyric_line as a separate line, including blank lines
    between verses where the markup provides them.
    """
    root = soup.select_one("#song-lyric-pre, #song-lyric, .hopamchuan_lyric")
    if not root:
        return ""

    # If the wrapper exists, process its visual lyric lines in order.
    line_nodes = root.select(".chord_lyric_line") if hasattr(root, "select") else []
    lines = []
    if line_nodes:
        for line_node in line_nodes:
            # Only collect lyric spans, never chord spans or neighboring UI text.
            lyric_parts = [span.get_text("", strip=False) for span in line_node.select(".hopamchuan_lyric")]
            line = "".join(lyric_parts).replace("\xa0", " ")
            line = re.sub(r"[ \t]+", " ", line).strip()
            if line:
                lines.append(line)
            elif "empty_line" in (line_node.get("class") or []):
                lines.append("")
        return _remove_lyrics_noise("\n".join(lines))

    # Fallback for pages where only the lyric spans can be found.
    lyric_spans = root.select(".hopamchuan_lyric")
    if lyric_spans:
        return _remove_lyrics_noise("".join(x.get_text("", strip=False) for x in lyric_spans))
    return ""


def extract_lyrics(soup):
    """Extract complete lyrics, preferring HopAmChuan's dedicated lyric spans."""
    # This is the most precise source on HopAmChuan: it contains lyric text
    # separated from chord annotations and unrelated page controls.
    dedicated_lyrics = _lyrics_from_hopamchuan_class(soup)
    if len(dedicated_lyrics) >= config.MIN_LYRICS_CHARS:
        return dedicated_lyrics

    selectors = [
        "#song-content", "#song-content-container", ".song-content",
        ".song-content-container", ".chord-content", ".chord-content-container",
        ".song-lyric", ".lyrics", "[class*='song-content']",
        "[class*='chord-content']", "[class*='lyric']",
    ]
    candidates = []
    seen = set()
    for selector in selectors:
        for node in soup.select(selector):
            if id(node) in seen:
                continue
            seen.add(id(node))
            clone = BeautifulSoup(str(node), "html.parser")
            for bad in clone.select(
                "script, style, button, nav, [class*='report'], [class*='author'], [class*='user']"
            ):
                bad.decompose()
            text = _remove_lyrics_noise(_text_lines(clone))
            if len(text) >= config.MIN_LYRICS_CHARS:
                candidates.append(text)

    for node in soup.find_all(attrs={"itemprop": re.compile(r"lyrics|text", re.I)}):
        text = _remove_lyrics_noise(_text_lines(node))
        if len(text) >= config.MIN_LYRICS_CHARS:
            candidates.append(text)

    # Open Graph description can contain lyrics or a long preview. Keep its
    # original newlines; meta_content() is intentionally not used here because
    # it collapses whitespace and can merge all lyric lines into one line.
    description = meta_content_raw(
        soup, "og:description", "twitter:description", "description"
    )
    if description:
        description = BeautifulSoup(description, "html.parser").get_text("", strip=False)
        description = _remove_lyrics_noise(description)
        if len(description) >= config.MIN_LYRICS_CHARS:
            candidates.append(description)

    # Nested containers and metadata previews may overlap. Select the longest
    # cleaned candidate so a short nested block doesn't truncate full lyrics.
    return max(candidates, key=len) if candidates else ""


def _title_from_song_page(soup):
    """Extract the song title from the song heading or page metadata."""
    # Prefer the visible heading on a song page, avoiding the full navigation title.
    for selector in (
        "h1.song-title", ".song-title h1", "h1[itemprop='name']",
        "main h1", "article h1", "h1",
    ):
        node = soup.select_one(selector)
        if node:
            value = clean_text(node.get_text(" ", strip=True))
            if value:
                value = re.sub(r"\s*[-|–—:]\s*(?:Hợp Âm Chuẩn|Hop Am Chuan).*?$", "", value, flags=re.I)
                if value:
                    return value

    # Fallback to Open Graph/title metadata and remove the site suffix.
    value = meta_content(soup, "og:title", "twitter:title")
    if not value and soup.title:
        value = clean_text(soup.title.get_text(" ", strip=True))
    value = re.sub(r"\s*[-|–—:]\s*(?:Hợp Âm Chuẩn|Hop Am Chuan).*?$", "", value, flags=re.I)
    return value.strip(" -|–—:")


_MISSING_ARTIST = {"n/a", "na", "n.a", "n.a.", "không rõ", "khong ro", "chưa rõ", "unknown", "-", "--", "?"}


def normalize_artist(name):
    """Trả về None nếu tác giả là "N/A" hoặc giá trị rỗng/không rõ."""
    name = clean_text(name)
    if not name or name.casefold() in _MISSING_ARTIST:
        return None
    return name


def _artist_from_song_page(soup):
    cell = _find_labeled_value(soup, "Tác giả")
    if cell:
        for a in cell.find_all("a", href=True):
            name = clean_text(a.get_text(" ", strip=True))
            if name and name.casefold() not in {"xem thêm", "xem tất cả"}:
                return name
    for key in ("author", "music:musician"):
        value = meta_content(soup, key)
        if value:
            return value
    return ""


def _genre_from_song_page(soup):
    cell = _find_labeled_value(soup, "Thể loại")
    if cell:
        value = clean_text(cell.get_text(" ", strip=True))
        value = re.sub(r"^Thể loại\s*[:：]\s*", "", value, flags=re.I)
        return value[:100] or ""
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
    # Trang có mục "Tác giả" nhưng ghi N/A -> giữ bài, lưu artist = NULL.
    artist = normalize_artist(artist)
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
