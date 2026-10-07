# =========================================================
# spotify_parser.py  –  metadata bài hát / album / playlist của Spotify
# =========================================================
# Chỉ đọc các thẻ <meta> mà Spotify trả về cho trình duyệt không chạy
# JavaScript (Requests). Thứ tự thẻ thực tế (lần chạy 05/10/2026):
#
#   Trang bài hát (/track/<id>):
#     og:title                    tên bài
#     og:description              "Ca sĩ · Album · Song · Năm"
#     music:duration              thời lượng (giây)
#     music:album                 link album;  music:album:track = số thứ tự bài
#     music:release_date          ngày phát hành (YYYY-MM-DD)
#     music:musician              link trang nghệ sĩ (mỗi nghệ sĩ một thẻ)
#     music:musician_description  tên nghệ sĩ, cách nhau bởi ", "
#
#   Trang album / playlist (/album/<id>, /playlist/<id>):
#     og:title, og:description    tên, "Ca sĩ · album · 2013 · 18 songs"
#     music:musician              nghệ sĩ của album
#     music:release_date          ngày phát hành (album)
#     music:song                  các bài trong album / playlist (theo thứ tự)
#     music:creator               người tạo playlist  -> KHÔNG lưu
#
# Không có lời bài hát và thể loại (chỉ hiện khi đăng nhập / chạy JavaScript).
# Không lưu dữ liệu người dùng (kể cả người tạo playlist).

import hashlib
import re
from urllib.parse import urlparse

SPOTIFY_PATH = re.compile(r"^/(track|album|playlist|artist|user)/([A-Za-z0-9]+)$")

# "Tên - song and lyrics by Ca sĩ", "Tên - Single by Ca sĩ", "Tên - Album by ..."
TITLE_SUFFIX = re.compile(
    r"\s+-\s+(?:song and lyrics by|song by|bài hát và lời bài hát của|bài hát của|"
    r"single by|album by|ep by|compilation by|playlist by)\s+.*$", re.I)
SITE_SUFFIX = re.compile(r"\s*\|\s*Spotify\b.*$")       # " | Spotify", " | Spotify Playlist"
LISTEN_PREFIX = re.compile(r"^Listen to .+? on Spotify\.\s*")
YEAR = re.compile(r"(19|20)\d\d")
SONG_WORDS = {"song", "bài hát", "ca khúc"}


def kind_and_id(url):
    """https://open.spotify.com/track/<id> -> ("track", "<id>")"""
    match = SPOTIFY_PATH.match(urlparse(url or "").path)
    return (match.group(1), match.group(2)) if match else (None, None)


def sha256_bytes(data):
    return hashlib.sha256(data or b"").hexdigest()


def _clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def meta_values(soup, key):
    """Giá trị của mọi thẻ <meta property|name=key>, giữ thứ tự trong trang."""
    key = key.lower()
    values = []
    for tag in soup.find_all("meta", content=True):
        name = (tag.get("property") or tag.get("name") or "").lower()
        if name == key:
            values.append(_clean(tag["content"]))
    return values


def meta_value(soup, key):
    values = meta_values(soup, key)
    return values[0] if values else None


def ids_of(urls, kind):
    """Lấy id Spotify (không trùng, giữ thứ tự) từ các link cùng loại."""
    ids = []
    for url in urls:
        k, spotify_id = kind_and_id(url)
        if k == kind and spotify_id not in ids:
            ids.append(spotify_id)
    return ids


def _int(value):
    return int(value) if value and str(value).isdigit() else None


def parse_description(text):
    """
    "Sơn Tùng M-TP · Chúng Ta Của Hiện Tại · Song · 2020"  -> ("Sơn Tùng M-TP", "Chúng Ta Của Hiện Tại", 2020)
    "Listen to X on Spotify. Song · Sơn Tùng M-TP · 2013"  -> ("Sơn Tùng M-TP", None, 2013)   (kiểu cũ)
    Dấu chấm cuối được giữ vì là một phần tên nghệ sĩ (vd "Onionn.").
    """
    text = LISTEN_PREFIX.sub("", _clean(text))
    tokens = [t.strip() for t in re.split(r"\s*·\s*", text) if t.strip()]
    year = next((int(t.rstrip(".")) for t in reversed(tokens) if YEAR.fullmatch(t.rstrip("."))), None)

    for i, token in enumerate(tokens):
        if token.lower() not in SONG_WORDS:
            continue
        before, after = tokens[:i], tokens[i + 1:]
        if len(before) >= 2:                         # Ca sĩ · Album · Song · Năm
            return before[0], before[1], year
        if len(before) == 1:                         # Ca sĩ · Song · Năm
            return before[0], None, year
        if after and not YEAR.fullmatch(after[0].rstrip(".")):   # Song · Ca sĩ · Năm
            return after[0], None, year
    return None, None, year


def split_artists(raw, n_ids):
    """
    Tách chuỗi tên nghệ sĩ cho khớp với số id nghệ sĩ trong trang.
      1 id            -> cả chuỗi là 1 tên (tên có thể chứa dấu phẩy)
      nhiều id        -> tách theo dấu phẩy; không tách được thì [] (không rõ)
      không có id     -> tách theo dấu phẩy
    """
    raw = _clean(raw)
    if not raw:
        return []
    if n_ids == 1:
        return [raw]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if n_ids >= 2 and len(parts) < 2:
        return []
    return parts


def page_title(soup):
    title = meta_value(soup, "og:title")
    if not title and soup.title:
        text = SITE_SUFFIX.sub("", _clean(soup.title.get_text()))
        title = TITLE_SUFFIX.sub("", text).strip()
    return title or None


def parse_track(url, soup, crawled_utc, html_sha256=None):
    """Trả về dict cho bảng tracks, hoặc None nếu không phải trang bài hát / không có tên bài."""
    kind, track_id = kind_and_id(url)
    title = page_title(soup)
    if kind != "track" or not title:
        return None

    description = meta_value(soup, "og:description") or meta_value(soup, "description") or ""
    artist_from_description, album, year = parse_description(description)

    artist_ids = ids_of(meta_values(soup, "music:musician"), "artist")
    artist_text = meta_value(soup, "music:musician_description") or artist_from_description
    artists = split_artists(artist_text, len(artist_ids))

    album_ids = ids_of(meta_values(soup, "music:album"), "album")
    release_date = meta_value(soup, "music:release_date")
    if release_date and YEAR.match(release_date):
        year = int(release_date[:4])

    return {
        "track_id": track_id,
        "url": url,
        "title": title,
        "artists": artists,
        "artist_ids": artist_ids,
        "artist_text": artist_text or None,
        "album": album,
        "album_id": album_ids[0] if album_ids else None,
        "track_number": _int(meta_value(soup, "music:album:track")),
        "release_date": release_date,
        "year": year,
        "duration_sec": _int(meta_value(soup, "music:duration")),
        "html_sha256": html_sha256,
        "crawled_utc": crawled_utc,
    }


def parse_listing(url, soup, crawled_utc, html_sha256=None):
    """Album / playlist / nghệ sĩ: tên, nghệ sĩ, ngày phát hành và danh sách bài (theo thứ tự)."""
    kind, listing_id = kind_and_id(url)
    if kind not in ("album", "playlist", "artist"):
        return None
    return {
        "url": url,
        "kind": kind,
        "listing_id": listing_id,
        "title": page_title(soup),
        "description": meta_value(soup, "og:description") or meta_value(soup, "description"),
        "artist_ids": ids_of(meta_values(soup, "music:musician"), "artist"),
        "release_date": meta_value(soup, "music:release_date"),
        "track_ids": ids_of(meta_values(soup, "music:song"), "track"),
        "html_sha256": html_sha256,
        "crawled_utc": crawled_utc,
    }


# ---------------------------------------------------------
# Thử nhanh với một trang đã lưu từ trình duyệt:
#     python spotify_parser.py trang.html https://open.spotify.com/track/<id>
# ---------------------------------------------------------
if __name__ == "__main__":
    import json
    import sys

    from bs4 import BeautifulSoup

    if len(sys.argv) != 3:
        sys.exit("Cách dùng: python spotify_parser.py <file.html> <url Spotify>")
    with open(sys.argv[1], "rb") as f:
        raw = f.read()
    page_soup = BeautifulSoup(raw.decode("utf-8", "replace"), "html.parser")
    parse = parse_track if kind_and_id(sys.argv[2])[0] == "track" else parse_listing
    print(json.dumps(parse(sys.argv[2], page_soup, None, sha256_bytes(raw)), ensure_ascii=False, indent=2))
