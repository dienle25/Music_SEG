from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import json
import re


def clean(x):
    return re.sub(r"\s+", " ", x or "").strip()


def is_zing_domain(url):
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
        return host in {"zingmp3.vn", "www.zingmp3.vn"}
    except Exception:
        return False


def is_song_url(url):
    if not is_zing_domain(url):
        return False

    path = urlparse(url).path.lower()

    # URL bài hát hiện tại của Zing MP3 thường có dạng:
    # /bai-hat/<slug>/<encodeId>.html
    return (
        "/bai-hat/" in path
        and path.endswith(".html")
        and len(path.rstrip("/").split("/")) >= 3
    )


def extract_links(html, base):
    soup = BeautifulSoup(html, "html.parser")
    out = set()

    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if not href:
            continue

        u = urljoin(base, href).split("#")[0]

        if is_song_url(u):
            out.add(u)

    # Một số trang JS chứa URL Zing trong script/JSON.
    # Chỉ lấy URL bài hát chính xác, không lấy API/private URL.
    for match in re.findall(
        r'https?://(?:www\.)?zingmp3\.vn/bai-hat/[^"\'\\\s<>]+\.html',
        html,
        flags=re.I,
    ):
        u = match.replace("\\/", "/")
        if is_song_url(u):
            out.add(u)

    return out


def meta(soup, *names):
    for n in names:
        x = (
            soup.find("meta", attrs={"property": n})
            or soup.find("meta", attrs={"name": n})
        )
        if x and x.get("content"):
            return clean(x["content"])

    return ""


def jsonld_artist(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except Exception:
            continue

        items = data if isinstance(data, list) else [data]

        for item in items:
            if not isinstance(item, dict):
                continue

            artist = item.get("byArtist") or item.get("author")

            if isinstance(artist, dict):
                name = artist.get("name")
                if name:
                    return clean(name)

            if isinstance(artist, list):
                names = []
                for a in artist:
                    if isinstance(a, dict) and a.get("name"):
                        names.append(clean(a["name"]))
                if names:
                    return ", ".join(names)

    return ""



def extract_lyric_metadata(html, page_url):
    """
    Phát hiện nguồn lyrics và tạo metadata an toàn:
    - lyric_url
    - lyric_status
    - lyric_excerpt: đoạn ngắn để kiểm tra parser
    - lyric_chars: số ký tự phát hiện được
    - lyric_hash: SHA-256 của nội dung phát hiện

    Không lưu toàn bộ lời bài hát vào SQLite.
    """
    soup = BeautifulSoup(html, "html.parser")
    lyric_url = ""

    # 1) Link có chữ lyric / lời bài hát
    for a in soup.select("a[href]"):
        txt = clean(a.get_text(" ", strip=True)).lower()
        href = a.get("href", "").strip()
        if (
            "lyric" in txt
            or "lyrics" in txt
            or "lời bài hát" in txt
            or "/lyric" in href.lower()
        ):
            candidate = urljoin(page_url, href)
            if candidate.startswith(("http://", "https://")):
                lyric_url = candidate
                break

    # 2) Tìm URL lyric trong HTML/script
    if not lyric_url:
        patterns = [
            r'https?://[^"\'\s<>\\]+/lyric[^"\'\s<>\\]*',
            r'https?://[^"\'\s<>\\]+/lyrics[^"\'\s<>\\]*',
        ]
        for pattern in patterns:
            m = re.search(pattern, html, flags=re.I)
            if m:
                lyric_url = m.group(0).replace("\\/", "/")
                break

    if not lyric_url:
        return {
            "lyric_url": "",
            "lyric_status": "not_detected",
            "lyric_excerpt": "",
            "lyric_chars": 0,
            "lyric_hash": "",
        }

    # Nếu HTML hiện tại đã chứa vùng lyric, chỉ lấy đoạn ngắn để kiểm tra.
    candidates = []
    for sel in [
        '[class*="lyric"]',
        '[id*="lyric"]',
        '[class*="Lyric"]',
        '[id*="Lyric"]',
    ]:
        for node in soup.select(sel):
            txt = clean(node.get_text("\n", strip=True))
            if len(txt) >= 20:
                candidates.append(txt)

    lyric_text = max(candidates, key=len) if candidates else ""

    if lyric_text:
        normalized = re.sub(r"\s+", " ", lyric_text).strip()
        excerpt = normalized[:160]
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return {
            "lyric_url": lyric_url,
            "lyric_status": "detected_in_page",
            "lyric_excerpt": excerpt,
            "lyric_chars": len(normalized),
            "lyric_hash": digest,
        }

    return {
        "lyric_url": lyric_url,
        "lyric_status": "url_detected",
        "lyric_excerpt": "",
        "lyric_chars": 0,
        "lyric_hash": "",
    }


def parse_song(html, url):
    soup = BeautifulSoup(html, "html.parser")

    title = meta(
        soup,
        "og:title",
        "twitter:title",
    )

    if not title and soup.title:
        title = clean(soup.title.get_text(" ", strip=True))

    artist = jsonld_artist(soup)

    if not artist:
        for sel in [
            '[class*="artist"] a',
            '[class*="singer"] a',
            '[class*="artist"]',
            '[class*="singer"]',
            '[class*="name-singer"]',
        ]:
            n = soup.select_one(sel)
            if n:
                value = clean(n.get_text(" ", strip=True))
                if value:
                    artist = value
                    break

    thumb = meta(
        soup,
        "og:image",
        "twitter:image",
    )

    # Tìm thêm thumbnail trong JSON-LD nếu og:image không có.
    if not thumb:
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or script.get_text())
            except Exception:
                continue

            items = data if isinstance(data, list) else [data]

            for item in items:
                if isinstance(item, dict):
                    image = item.get("image")
                    if isinstance(image, str):
                        thumb = image
                        break
                    if isinstance(image, dict) and image.get("url"):
                        thumb = image["url"]
                        break

            if thumb:
                break

    duration = ""

    for sel in [
        '[class*="duration"]',
        '[class*="time"]',
    ]:
        n = soup.select_one(sel)
        if n:
            value = clean(n.get_text(" ", strip=True))
            if value:
                duration = value
                break

    lyric = extract_lyric_metadata(html, url)

    return {
        "title": title,
        "artist": artist,
        "category": "music",
        "url": url,
        "thumbnail": thumb,
        "duration": duration,
        "lyric_url": lyric["lyric_url"],
        "lyric_status": lyric["lyric_status"],
        "lyric_excerpt": lyric["lyric_excerpt"],
        "lyric_chars": lyric["lyric_chars"],
        "lyric_hash": lyric["lyric_hash"],
        "_valid": bool(
            title
            and url
            and thumb
        ),
    }
