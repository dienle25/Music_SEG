# =========================================================
# song_parser.py  –  tách thông tin BÀI HÁT cho bảng songs
# =========================================================
# Từ HTML của một trang bài hát lấy ra 5 cột:
#   title, artist, album, genre, lyrics
#
# Mỗi cột thử lần lượt nhiều nguồn, nguồn nào có dữ liệu trước thì dùng:
#   1. JSON-LD (schema.org MusicRecording)
#   2. Spotify: mô tả "Nghệ sĩ · Album · Song · Năm" trong thẻ <meta>
#   3. Nhãn chữ trong trang:  "Ca sĩ: ...", "Thể loại: ...", "Album: ..."
#   4. Tiêu đề trang dạng "Tên bài - Ca sĩ - Tên website"
#   5. Lời bài hát: khối có chữ "lyric" trong id/class, hoặc khối đứng sau
#      tiêu đề "Lời bài hát" / "Lyrics" / "Chord by ...", hoặc khối chứa
#      hợp âm dạng [Am] (Hợp Âm Chuẩn)
# Cột nào không tìm thấy thì trả về None (NULL trong SQLite).
#
# Chạy thử trên một file HTML đã lưu từ trình duyệt:
#   python song_parser.py trang.html https://nhac.vn/bai-hat/...

import copy
import json
import re
import sys
import unicodedata
from urllib.parse import urlparse

from bs4 import Comment, Tag

import config
from parser import get_meta
from url_filter import canonical_host


# ---------------------------------------------------------
# HÀM DÙNG CHUNG
# ---------------------------------------------------------

# Chữ nằm trong các thẻ này (menu, script, chân trang...) không phải nội dung bài hát
_SKIP_TAGS = {"script", "style", "noscript", "template", "head", "title",
              "nav", "footer", "select", "option"}


def _clean(text):
    """Gộp khoảng trắng (kể cả xuống dòng), bỏ khoảng trắng đầu / cuối."""
    return re.sub(r"\s+", " ", text or "").strip()


def _key(text):
    """Dạng dùng để so sánh hai chuỗi: không phân biệt hoa / thường."""
    return _clean(unicodedata.normalize("NFC", text or "")).casefold()


def _skipped(node):
    """Chữ nằm trong script / menu / chân trang... thì bỏ qua."""
    return any(parent.name in _SKIP_TAGS for parent in node.parents)


def main_h1(soup):
    """Chữ của thẻ <h1> đầu tiên nằm trong nội dung trang (không tính menu)."""
    for h1 in soup.find_all("h1"):
        if any(parent.name in _SKIP_TAGS for parent in h1.parents):
            continue
        text = _clean(h1.get_text(" ", strip=True))
        if text:
            return text
    return ""


# ---------------------------------------------------------
# NGUỒN 1: JSON-LD  (schema.org MusicRecording)
# ---------------------------------------------------------

def _walk(data):
    """Đi qua mọi dict nằm trong JSON (kể cả @graph và danh sách lồng nhau)."""
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from _walk(value)
    elif isinstance(data, list):
        for value in data:
            yield from _walk(value)


def _names(value):
    """Tên trong JSON-LD: chuỗi, dict có 'name', hoặc danh sách gồm hai kiểu đó."""
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, dict):
        return _clean(str(value.get("name", "")))
    if isinstance(value, list):
        return ", ".join(name for name in (_names(v) for v in value) if name)
    return ""


def json_ld_song(soup):
    """Đọc <script type="application/ld+json"> có @type = MusicRecording."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except ValueError:
            continue

        for node in _walk(data):
            kinds = node.get("@type")
            if "MusicRecording" not in (kinds if isinstance(kinds, list) else [kinds]):
                continue

            lyrics = node.get("lyrics")
            if isinstance(lyrics, dict):
                lyrics = lyrics.get("text")

            return {
                "title": _names(node.get("name")),
                "artist": _names(node.get("byArtist")),
                "album": _names(node.get("inAlbum")),
                "genre": _names(node.get("genre")),
                "lyrics": lyrics if isinstance(lyrics, str) else "",
            }
    return {}


# ---------------------------------------------------------
# NGUỒN 2: Spotify – mô tả trong thẻ <meta>
# ---------------------------------------------------------

# Chữ báo loại trang trong mô tả của Spotify
_SONG_WORDS = {"song", "bài hát", "ca khúc"}


def spotify_description(soup):
    """
    Mô tả của trang track Spotify:
        "Sơn Tùng M-TP · Chúng Ta Của Hiện Tại · Song · 2020"
         ca sĩ          · album                 · loại  · năm
    Trả về (artist, album); thiếu cái nào thì trả None.
    """
    text = get_meta(soup, "og:description") or get_meta(soup, "description")
    text = re.sub(r"^Listen to .+? on Spotify\.\s*", "", text)      # kiểu mô tả cũ
    tokens = [t.strip(" .") for t in re.split(r"\s*·\s*", text) if t.strip(" .")]

    for i, token in enumerate(tokens):
        if _key(token) not in _SONG_WORDS:
            continue
        before, after = tokens[:i], tokens[i + 1:]
        if len(before) >= 2:                     # Ca sĩ · Album · Song · Năm
            return before[0], before[1]
        if len(before) == 1:                     # Ca sĩ · Song · Năm
            return before[0], None
        if after and not after[0].isdigit():     # Song · Ca sĩ · Năm  (kiểu cũ)
            return after[0], None
    return None, None


# ---------------------------------------------------------
# NGUỒN 3: nhãn chữ  "Thể loại: Nhạc Trẻ"
# ---------------------------------------------------------

_LABEL_LIKE = re.compile(r"^[^:：]{1,30}[:：]$")        # "Lượt nghe:" -> là một nhãn khác


def _tidy_value(value):
    """Làm sạch giá trị sau nhãn; trả None nếu trông không phải giá trị thật."""
    value = re.sub(r"\s+,", ",", _clean(value)).strip(" :：|-–,;")
    if not value or len(value) > 150:
        return None
    if re.match(r"^[^:：]{1,25}[:：]\s*\S", value):         # "Lượt nghe: 5" -> nhãn khác
        return None
    return value


def _value_after(node):
    """Chữ đứng ngay sau nhãn: cùng phần tử cha, hoặc ở ô / phần tử anh em kế tiếp."""
    start = node
    for _ in range(3):                  # nhãn có thể nằm riêng trong <span> / <td> / <dt>
        pieces = []
        for sibling in start.next_siblings:
            if isinstance(sibling, Comment):
                continue
            if isinstance(sibling, Tag):
                text = _clean(sibling.get_text(" ", strip=True))
            else:
                text = _clean(str(sibling))
            text = text.lstrip(":：").strip()
            if not text or text in ("|", "-", "–"):
                continue
            if _LABEL_LIKE.match(text):          # gặp nhãn kế tiếp -> dừng
                break
            pieces.append(text)
        if pieces:
            return " ".join(pieces)
        start = start.parent
        if start is None:
            break
    return ""


def find_label(soup, labels):
    """
    Tìm giá trị đi sau nhãn chữ, ví dụ "Thể loại: Nhạc Trẻ". Nhận các kiểu HTML:
        <li>Thể loại: Nhạc Trẻ</li>
        <li><span>Thể loại:</span> <a>Nhạc Trẻ</a></li>
        <tr><td>Thể loại:</td><td>Nhạc Trẻ</td></tr>
        <dt>Thể loại:</dt><dd>Nhạc Trẻ</dd>
        <b>Thể loại</b>: Nhạc Trẻ
    Nhãn PHẢI có dấu ":" để không nhầm với mục menu như "Album", "Nghệ sĩ".
    """
    if not labels:
        return None

    alternatives = "|".join(re.escape(l) for l in sorted(labels, key=len, reverse=True))
    inline = re.compile(r"^\s*(?:%s)\s*[:：]\s*(?P<value>\S.*)$" % alternatives, re.I | re.S)
    alone = re.compile(r"^\s*(?:%s)\s*[:：]?\s*$" % alternatives, re.I)
    starts_with_label = re.compile(r"^\s*(?:%s)" % alternatives, re.I)

    for node in soup.find_all(string=starts_with_label):
        if _skipped(node):
            continue

        text = unicodedata.normalize("NFC", str(node))
        match = inline.match(text)
        if match:
            value = match.group("value")
        elif alone.match(text):
            next_text = node.find_next(string=lambda s: s.strip()) or ""
            has_colon = (text.rstrip().endswith((":", "："))
                         or next_text.lstrip().startswith((":", "：")))
            if not has_colon:
                continue
            value = _value_after(node)
        else:
            continue

        value = _tidy_value(value)
        if value:
            return value
    return None


# ---------------------------------------------------------
# NGUỒN 4: tiêu đề trang  "Tên bài - Ca sĩ - Tên website"
# ---------------------------------------------------------

_NOISE = {_key(word) for word in config.TITLE_NOISE}

_PREFIX = re.compile(
    r"^(?:%s)\s*[:\-–]?\s+(?=\S)" % "|".join(
        re.escape(p) for p in sorted(config.TITLE_PREFIXES, key=len, reverse=True)),
    re.I)

# "(Hợp âm cơ bản)", "[Lyrics]" ở cuối một đoạn tiêu đề
_GENERIC_PAREN = re.compile(
    r"\s*[(\[]\s*(?:hợp âm|chord|lyrics?|lời bài hát)[^)\]]*[)\]]\s*$", re.I)

# Dấu ngăn cách các đoạn của tiêu đề (có khoảng trắng hai bên, nên "M-TP" không bị cắt)
_SEPARATOR = re.compile(r"\s+[-–—|•·]\s+|\s*\|\s*")

# Tiêu đề Spotify:  "Lạc Trôi - song and lyrics by Sơn Tùng M-TP | Spotify"
_SPOTIFY_BY = re.compile(
    r"\s+-\s+(?:song and lyrics by|bài hát và lời bài hát của|bài hát và lời của|bài hát của)\s+",
    re.I)


def title_parts(raw, strip_prefix=True):
    """
    Cắt tiêu đề thành các đoạn và bỏ phần thừa:
      "Hợp âm Lạc Trôi - Sơn Tùng M-TP (Hợp âm cơ bản) - Hợp Âm Chuẩn"
          -> ["Lạc Trôi", "Sơn Tùng M-TP"]
      "Lạc Trôi - Sơn Tùng M-TP - mp3 download | lyric - NhacCuaTui"
          -> ["Lạc Trôi", "Sơn Tùng M-TP"]
    """
    raw = _clean(raw)
    raw = _SPOTIFY_BY.sub(" - ", raw)                  # "Tên bài - song and lyrics by Ca sĩ"

    parts = []
    for i, segment in enumerate(_SEPARATOR.split(raw)):
        segment = _GENERIC_PAREN.sub("", segment).strip()
        if i == 0 and strip_prefix:
            segment = _PREFIX.sub("", segment, count=1).strip()
        if segment and _key(segment) not in _NOISE:
            parts.append(segment)
    return parts


def _split_title(parts, artist, all_parts):
    """Từ các đoạn của một tiêu đề, tách ra (tên bài hát, ca sĩ suy ra từ tiêu đề)."""
    # Đã biết ca sĩ: bỏ đoạn trùng tên ca sĩ, phần còn lại là tên bài
    if artist:
        artist_key = _key(artist)
        rest = [
            part for i, part in enumerate(parts)
            if _key(part) != artist_key
            and not (len(parts) > 1 and i == len(parts) - 1 and _key(part).startswith(artist_key))
        ]
        return (" - ".join(rest) or parts[0]), None

    # Chưa biết ca sĩ, tiêu đề dạng "Tên bài - Ca sĩ": đoạn cuối là ca sĩ
    if len(parts) >= 2:
        return " - ".join(parts[:-1]), parts[-1]

    # Tiêu đề chỉ có tên bài: tìm ca sĩ ở tiêu đề khác ("Lạc Trôi - Sơn Tùng M-TP")
    title = parts[0]
    for other in all_parts:
        if other is parts or len(other) < 2:
            continue
        names = [p for p in other if _key(p) != _key(title)]
        if names and len(names) < len(other):       # tên bài có nằm trong tiêu đề này
            return title, names[-1]
    return title, None


def pick_title_artist(candidates, reference, artist=None):
    """
    candidates: các chuỗi tiêu đề thô, ưu tiên từ trên xuống (h1, og:title, <title>)
    reference : chữ của JSON-LD + og:title + <title>; tên bài hát thật phải nằm trong đó
                (để không lấy nhầm <h1> là logo hay khẩu hiệu của website)
    artist    : ca sĩ đã biết từ nguồn khác, hoặc None
    Trả về (title, ca sĩ suy ra từ tiêu đề) hoặc (None, None).
    """
    parsed = [title_parts(text) for text in candidates if text]
    parsed = [parts for parts in parsed if parts]
    if not parsed:
        return None, None

    results = [_split_title(parts, artist, parsed) for parts in parsed]
    for title, from_title in results:
        if not reference or _key(title) in reference:
            return title, from_title
    return results[0]


# ---------------------------------------------------------
# LỜI BÀI HÁT
# ---------------------------------------------------------

# Hợp âm: Am, Em, F#m7, Bb, Dsus4, Gadd9, Cmaj7, Am7/G, D/F#...
_CHORD = (r"[A-G][#b]?(?:maj|min|dim|aug|sus|add|m)?\d{0,2}"
          r"(?:(?:maj|sus|add)?\d{1,2})*(?:/[A-G][#b]?)?")
_BRACKET_CHORD = re.compile(r"\[\s*%s\s*\]" % _CHORD)            # [Am]
_CHORD_ONLY = re.compile(r"^%s(?:\s+%s)*$" % (_CHORD, _CHORD))

_BLOCK_TAGS = ["p", "div", "li", "tr", "section", "article", "pre",
               "h1", "h2", "h3", "h4", "h5", "h6"]
_HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
_INLINE_TAGS = {"span", "a", "b", "i", "u", "em", "strong", "sup", "sub",
                "font", "code", "label", "small"}

# Tiêu đề đứng trước phần lời:  "Lời bài hát", "Lyrics", "Hợp âm dễ - Chord by ..."
_LYRIC_HEADING = re.compile(
    r"^\s*(?:lời bài hát|lời nhạc|lyrics?)\b|\bchord by\b|^\s*hợp âm dễ\b", re.I)

# Thử lần lượt từng nhóm selector: nhóm đầu tiên có lời bài hát thì dùng
_LYRIC_SELECTORS = [
    # tên hay gặp
    '[itemprop="lyrics"], #divLyric, .pd_lyric, #lyric, #lyrics, .lyric, .lyrics, .song-lyric',
    # id / class bất kỳ có chữ "lyric" hoặc "loi-bai-hat" (không phân biệt hoa / thường)
    '[id*="lyric" i], [class*="lyric" i], [id*="loi-bai-hat" i], '
    '[class*="loi-bai-hat" i], [class*="loibaihat" i]',
]

_NO_LYRICS = re.compile(
    r"chưa có lời|đang cập nhật|chưa cập nhật|no lyrics|updating|not available", re.I)


def _is_chord_tag(tag):
    """Thẻ nhỏ chỉ chứa một hợp âm, ví dụ <span class="chord">Am</span>."""
    if tag.name not in _INLINE_TAGS and "chord" not in tag.name:
        return False
    text = tag.get_text(strip=True).strip("[]() ")
    if not text or len(text) > 12 or not _CHORD_ONLY.match(text):
        return False
    # chỉ coi là hợp âm khi tên thẻ / thuộc tính có chữ "chord" (tránh xoá chữ "Em", "A" trong lời)
    attributes = tag.name + " " + " ".join(
        " ".join(v) if isinstance(v, list) else str(v) for v in tag.attrs.values())
    return "chord" in attributes.lower()


def _block_text(tag):
    """Chữ của một khối: giữ xuống dòng ở <br> và cuối mỗi khối, bỏ hợp âm dạng thẻ."""
    tag = copy.copy(tag)                       # không sửa cây HTML gốc (còn dùng để lấy link)

    for junk in tag.find_all(["script", "style", "noscript", "template"]):
        junk.extract()
    for element in tag.find_all(True):
        if _is_chord_tag(element):
            element.extract()
    for br in tag.find_all("br"):
        br.replace_with("\n")
    for block in tag.find_all(_BLOCK_TAGS):
        block.append("\n")

    return tag.get_text("")


def _link_ratio(tag):
    """Tỉ lệ chữ nằm trong thẻ <a>: danh sách bài hát liên quan có tỉ lệ rất cao."""
    total = len(tag.get_text(strip=True))
    links = sum(len(a.get_text(strip=True)) for a in tag.find_all("a"))
    return links / total if total else 0


def clean_lyrics(text):
    """Bỏ hợp âm [Am], dòng tiêu đề đầu khối, dòng trống thừa; trả "" nếu không phải lời bài hát."""
    text = _BRACKET_CHORD.sub("", text or "")
    text = re.sub(r"\[\s*\]", "", text)

    lines = [re.sub(r"[ \t]+", " ", line).strip()
             for line in text.replace("\r", "").split("\n")]

    # bỏ dòng tiêu đề ở đầu khối: "Lời bài hát", "Lyrics:", "Hợp âm dễ - Chord by ..."
    while lines and (not lines[0] or (len(lines[0]) <= 80 and _LYRIC_HEADING.search(lines[0]))):
        lines.pop(0)

    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

    if len(text) < 30:
        return ""
    if len(text) < 150 and _NO_LYRICS.search(text):      # "Chưa có lời bài hát"
        return ""
    return text


def _usable_box(tag):
    """Khối có thể là phần lời: không phải khung cả trang (chứa <h1>, menu, chân trang)."""
    if tag.name in ("html", "body", "main", "header", "footer", "nav", "aside", "form"):
        return False
    return tag.find(["h1", "nav", "footer"]) is None


def lyrics_by_selector(soup):
    """Khối có id / class chứa chữ "lyric" (hoặc "loi-bai-hat")."""
    for selector in _LYRIC_SELECTORS:
        try:
            matches = soup.select(selector)
        except Exception:                       # selector không hợp lệ ở bản soupsieve cũ
            continue

        found = [tag for tag in matches if _usable_box(tag)]
        ids = {id(tag) for tag in found}
        outer = [tag for tag in found if not any(id(p) in ids for p in tag.parents)]

        texts = [clean_lyrics(_block_text(tag)) for tag in outer]
        texts = [text for text in texts if text]
        if texts:
            return max(texts, key=len)
    return ""


def lyrics_by_heading(soup):
    """Phần nằm sau tiêu đề "Lời bài hát" / "Lyrics" / "Chord by ..." cho tới tiêu đề kế tiếp."""
    for node in soup.find_all(string=_LYRIC_HEADING):
        heading = node.parent
        if heading is None or _skipped(node) or heading.name in ("a", "button"):
            continue
        if len(_clean(heading.get_text(" ", strip=True))) > 80:
            continue                            # đây là đoạn văn dài, không phải tiêu đề

        start = heading                         # tiêu đề có thể nằm trong một <div> riêng
        for _ in range(3):
            if start.find_next_sibling() is not None:
                break
            start = start.parent
            if start is None:
                break
        if start is None:
            continue

        pieces = []
        for sibling in start.find_next_siblings():
            if sibling.name in _HEADING_TAGS or sibling.find(_HEADING_TAGS):
                break
            if _link_ratio(sibling) > 0.6:
                continue
            pieces.append(_block_text(sibling))

        text = clean_lyrics("\n".join(pieces))
        if text:
            return text
    return ""


def lyrics_by_chords(soup):
    """Lời kèm hợp âm [Am]...: lấy khối nhỏ nhất chứa tất cả các hợp âm đó."""
    nodes = [n for n in soup.find_all(string=_BRACKET_CHORD) if not _skipped(n)]
    if len(nodes) < 3:
        return ""

    common = None
    for level in zip(*[list(reversed(list(n.parents))) for n in nodes]):
        if all(tag is level[0] for tag in level):
            common = level[0]
        else:
            break
    if common is None or common.name in ("[document]", "html", "body"):
        return ""
    return clean_lyrics(_block_text(common))


def extract_lyrics(soup, from_json_ld=None):
    """Thử lần lượt: JSON-LD -> id/class "lyric" -> tiêu đề "Lời bài hát" -> hợp âm [Am]."""
    text = clean_lyrics(from_json_ld)
    for finder in (lyrics_by_selector, lyrics_by_heading, lyrics_by_chords):
        if text:
            break
        text = finder(soup)
    return text


# ---------------------------------------------------------
# TỔNG HỢP
# ---------------------------------------------------------

def extract_song_data(url, soup, crawled_at):
    """
    Trả về dict cho bảng songs:
        url, title, artist, album, genre, lyrics, crawled_at
    hoặc None nếu không tìm được tên bài hát.
    Cột nào không có dữ liệu thì để None.
    """
    site = config.DOMAINS.get(canonical_host(urlparse(url).netloc), {})

    ld = json_ld_song(soup)
    spotify_artist, spotify_album = spotify_description(soup)

    og_title = get_meta(soup, "og:title")
    page_title = _clean(soup.title.get_text()) if soup.title else ""
    h1_text = main_h1(soup)

    # Trang nhạc (og:type = music.song, như Spotify): og:title là tên bài chuẩn nhất
    if get_meta(soup, "og:type").lower().startswith("music."):
        candidates = [ld.get("title"), og_title, h1_text, page_title]
    else:
        candidates = [ld.get("title"), h1_text, og_title, page_title]
    reference = " ".join(_key(t) for t in (ld.get("title"), og_title, page_title) if t)

    # ----- ca sĩ + tên bài -----
    artist = (ld.get("artist") or spotify_artist
              or find_label(soup, config.SONG_LABELS["artist"]))
    title, title_artist = pick_title_artist(candidates, reference, artist)
    if not title:
        return None
    artist = (artist or title_artist
              or find_label(soup, config.SONG_LABELS["author"]))

    # ----- album, thể loại -----
    album = (ld.get("album") or spotify_album
             or find_label(soup, config.SONG_LABELS["album"]))
    genre = ld.get("genre") or find_label(soup, config.SONG_LABELS["genre"])

    # ----- lời bài hát -----
    lyrics = None
    if site.get("has_lyrics", True):
        lyrics = extract_lyrics(soup, ld.get("lyrics"))

    return {
        "url": url,
        "title": title,
        "artist": artist or None,
        "album": album or None,
        "genre": genre or None,
        "lyrics": lyrics or None,
        "crawled_at": crawled_at,
    }


# ---------------------------------------------------------
# CHẠY THỬ:  python song_parser.py trang.html https://nhac.vn/bai-hat/...
# ---------------------------------------------------------

if __name__ == "__main__":
    from bs4 import BeautifulSoup

    if len(sys.argv) != 3:
        sys.exit("Cách dùng: python song_parser.py <file.html> <URL của trang>")

    with open(sys.argv[1], encoding="utf-8") as f:
        page = BeautifulSoup(f.read(), "html.parser")

    song = extract_song_data(sys.argv[2], page, "")
    if song is None:
        sys.exit("Không tìm thấy tên bài hát trong trang này.")

    for column in ("title", "artist", "album", "genre"):
        print(f"{column:<8}: {song[column]}")
    lyrics = song["lyrics"] or ""
    print(f"{'lyrics':<8}: {len(lyrics)} ký tự")
    print(lyrics[:300])
