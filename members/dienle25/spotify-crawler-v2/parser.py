# =========================================================
# parser.py  –  TASK 4 + 5: lấy thông tin trang & trích xuất link
# =========================================================

import re
import base64
import warnings
from datetime import datetime
from urllib.parse import urlparse

from bs4 import XMLParsedAsHTMLWarning

import config
from url_filter import normalize_url, is_music_url

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


# Link tuyệt đối tới các domain đang crawl (nằm trong JSON / script)
_HOSTS = sorted(set(config.ALLOWED_DOMAINS) | set(config.DOMAIN_ALIASES), key=len, reverse=True)
EMBEDDED_URL = re.compile(
    r"https?://(?:%s)/[^\s\"'<>\\)]*" % "|".join(re.escape(h) for h in _HOSTS)
)

# Đường dẫn tương đối nằm trong dấu nháy:  "/track/1J3SmWwlYAG68LGKr86MVH"
QUOTED_PATH = re.compile(r"""["'](/[^"'\s<>\\]{1,200})["']""")

# URI của Spotify:  spotify:track:1J3SmWwlYAG68LGKr86MVH
SPOTIFY_URI = re.compile(r"spotify:(artist|album|track|playlist):([A-Za-z0-9]{22})")

BASE64_TEXT = re.compile(r"^[A-Za-z0-9+/=\s]{200,}$")


# ---------------------------------------------------------
# META TAGS
# ---------------------------------------------------------

def get_meta(soup, key):
    tag = (soup.find("meta", attrs={"property": key})
           or soup.find("meta", attrs={"name": key}))
    return tag.get("content", "").strip() if tag else ""


def read_meta_robots(soup, headers=None):
    """
    Đọc <meta name="robots"> và header X-Robots-Tag.
      noindex  -> không lưu trang vào database
      nofollow -> không đi theo link trong trang
    Trả về (can_index, can_follow, giá_trị_gốc).
    """
    values = [
        tag.get("content", "")
        for tag in soup.find_all("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    ]

    if headers and headers.get("X-Robots-Tag"):
        values.append(headers["X-Robots-Tag"])

    raw = ", ".join(v for v in values if v)
    tokens = {t for t in re.split(r"[,\s]+", raw.lower()) if t}

    can_index = not ({"noindex", "none"} & tokens)
    can_follow = not ({"nofollow", "none"} & tokens)

    return can_index, can_follow, raw


# ---------------------------------------------------------
# TASK 4 – EXTRACT PAGE INFORMATION
# ---------------------------------------------------------

def extract_title(soup):
    h1 = soup.find("h1")
    candidates = [
        soup.title.get_text(strip=True) if soup.title else "",
        get_meta(soup, "og:title"),
        h1.get_text(" ", strip=True) if h1 else "",
    ]

    # <title> quá chung chung (chỉ một từ) -> thử og:title, rồi <h1>
    for text in candidates:
        if " " in text:
            return text

    return next((text for text in candidates if text), "")


def extract_text(soup):
    """Mô tả trong <meta> + chữ hiển thị trên trang (chưa tiền xử lý)."""
    parts = [
        get_meta(soup, "description"),
        get_meta(soup, "og:description"),
        soup.get_text(separator="\n", strip=True),
    ]

    lines = []
    for part in parts:
        lines.extend(line.strip() for line in part.splitlines())

    # bỏ dòng rỗng / dòng trùng, giữ nguyên thứ tự
    return "\n".join(dict.fromkeys(line for line in lines if line))


def extract_page_data(url, depth, status_code, soup):
    return {
        "url": url,
        "domain": urlparse(url).netloc,
        "title": extract_title(soup),
        "content": extract_text(soup),
        "depth": depth,
        "status_code": status_code,
        "crawled_at": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------
# TASK 5 – EXTRACT HYPERLINKS
# ---------------------------------------------------------

def _decoded_scripts(soup):
    """Một số site (Spotify) nhúng dữ liệu trang dạng base64 trong <script>."""
    for script in soup.find_all("script"):
        raw = (script.string or "").strip()
        if not BASE64_TEXT.match(raw):
            continue
        try:
            yield base64.b64decode(raw).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue


def extract_links(soup, html, current_url):
    """
    Trả về danh sách URL (đã chuẩn hoá, không trùng, giữ thứ tự xuất hiện).

    1. Thẻ <a href>                         – cách chuẩn
    2. <meta content="https://...">         – Spotify: music:song, music:album, music:musician
    3. URL / URI nhúng trong <script>/JSON  – phần render bằng JavaScript
       (chỉ nhận URL là trang âm nhạc của domain đang crawl)
    Không lấy link trang người dùng (/user/...).
    """
    links = []

    def add(href, only_music=False):
        url = normalize_url(current_url, href)
        if not url or url == current_url:
            return
        if urlparse(url).path.startswith("/user/"):      # trang cá nhân người dùng: không lưu
            return
        if only_music and not is_music_url(url):
            return
        links.append(url)

    # ---------- 1. <a href> ----------
    for tag in soup.find_all("a", href=True):
        add(tag["href"])

    # ---------- 2. <meta property="music:..."> ----------
    # Chỉ lấy giá trị là URL. Các thẻ music:* khác chứa số thứ tự bài, ngày
    # phát hành, thời lượng, tên ca sĩ... (bản 4 website lưu nhầm chúng thành
    # link dạng /track/1, /track/2026-07-12).
    for tag in soup.find_all("meta", content=True):
        key = (tag.get("property") or tag.get("name") or "").lower()
        content = tag["content"].strip()
        if key.startswith("music:") and content.lower().startswith(("http://", "https://")):
            add(content)

    # ---------- 3. URL trong script / JSON ----------
    blobs = [html] + list(_decoded_scripts(soup))
    for blob in blobs:
        # bỏ ký tự escape của JSON:  \/   \u002F   \"
        blob = (blob.replace("\\/", "/").replace("\\u002F", "/")
                    .replace("\\u002f", "/").replace('\\"', '"'))

        for match in EMBEDDED_URL.finditer(blob):
            add(match.group(0), only_music=True)

        for match in QUOTED_PATH.finditer(blob):
            add(match.group(1), only_music=True)

        for kind, spotify_id in SPOTIFY_URI.findall(blob):
            add(f"https://open.spotify.com/{kind}/{spotify_id}", only_music=True)

    return list(dict.fromkeys(links))
