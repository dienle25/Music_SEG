# =========================================================
# config.py  –  TASK 1: Seed URLs & cấu hình crawler (chỉ Spotify)
# =========================================================
# Bản chỉ crawl open.spotify.com, tách từ crawler 4 website của nhóm
# (bản 4 website vẫn nằm trong thư mục web-crawler/ trên GitHub).
# Ngoài bảng pages / links / songs theo đề bài, crawler lưu thêm metadata
# bài hát (bảng tracks) để dùng cho đề tài C2C-VN (xem export_c2c.py).
# Mọi thông số nằm ở file này, đổi cấu hình không cần sửa code.

import os

TOPIC = "Music (Âm nhạc) – nhạc Việt trên Spotify"
CRAWLER_VERSION = "spotify-1.0.0"

HOST = "open.spotify.com"

# Loại trang Spotify được crawl. Trang nghệ sĩ (artist) trả về HTML gần như
# rỗng, không có link (lần chạy 05/10/2026: 0 link) nên mặc định KHÔNG tải:
# tên và id nghệ sĩ đã có sẵn trong trang bài hát.
CRAWL_KINDS = ("playlist", "album", "track")
FETCH_ARTIST_PAGES = False          # True: tải cả trang /artist/<id>

DOMAINS = {
    HOST: {
        "name": "Spotify",
        "seeds": [
            "https://open.spotify.com/playlist/37i9dQZF1DWYPc4oQ0ynkq",   # Mãi Yêu Sơn Tùng M-TP
            "https://open.spotify.com/album/1V77kA4O1MlAUwfyBONfp1",      # Chúng Ta Của Hiện Tại
            # thêm playlist / album khác vào seeds.txt
        ],
        "allow": [
            r"^/(%s)/[A-Za-z0-9]{22}$" % "|".join(
                CRAWL_KINDS + (("artist",) if FETCH_ARTIST_PAGES else ())),
        ],
        "song_pages": [
            r"^/track/[A-Za-z0-9]{22}$",                     # bài hát (track)
        ],
        "has_lyrics": False,      # HTML chỉ có tiêu đề + mô tả, không có lời bài hát
        "rewrite": [
            # /intl-vi/track/ID -> /track/ID
            (r"^/intl-[\w-]+(/.*)$", r"\1"),
        ],
    },
}

# Seed bổ sung: mỗi dòng của seeds.txt là một link Spotify (playlist / album /
# track), phần sau dấu # là ghi chú. Seed trong seeds.txt được thêm sau seed
# ở trên, trùng thì bỏ qua.
SEEDS_FILE = "seeds.txt"


def _read_seeds_file():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SEEDS_FILE)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        lines = [line.split("#", 1)[0].strip() for line in f]
    return [line for line in lines if line]


SEED_URLS = list(dict.fromkeys(
    [url for site in DOMAINS.values() for url in site["seeds"]] + _read_seeds_file()
))
ALLOWED_DOMAINS = list(DOMAINS)
DOMAIN_ALIASES = {}


# ---------------------------------------------------------
# GIỚI HẠN CRAWL
# ---------------------------------------------------------

MAX_DEPTH = 6              # playlist (0) -> track (1) -> album (2) -> track (3) -> ...
MAX_PAGES = 5000           # số trang LƯU vào database
MAX_HOURS = 2.5            # dừng sau 2,5 giờ dù chưa đủ MAX_PAGES

REQUEST_TIMEOUT = 10       # giây
CRAWL_DELAY = 1.0          # giây nghỉ sau mỗi request

# HTTP 429 (Too Many Requests) / 503: chờ rồi thử lại, tôn trọng Retry-After.
RETRY_STATUSES = (429, 503)
MAX_RETRIES = 3
MAX_BACKOFF = 60           # giây; server bắt chờ lâu hơn -> dừng crawl
STOP_AFTER_CONSECUTIVE_FAILURES = 10   # lỗi liên tiếp (mạng, 403, 429, 5xx) -> dừng crawl


# File không phải trang HTML
BLOCKED_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".json", ".xml", ".zip", ".rar", ".pdf",
    ".mp3", ".mp4", ".m4a", ".flac", ".wav",
)


# ---------------------------------------------------------
# HTTP
# ---------------------------------------------------------

BOT_NAME = "SEG301-MusicCrawler"   # tên dùng khi kiểm tra robots.txt

# Một số site trả trang rỗng cho User-Agent lạ, nên dùng UA trình duyệt
# và vẫn gắn tên bot để website biết đây là crawler.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 "
    f"(compatible; {BOT_NAME}/1.0)"
)


# ---------------------------------------------------------
# LỜI BÀI HÁT (lyrics.py)
# ---------------------------------------------------------
# HTML công khai của Spotify không có lời bài hát -> lyrics.py tra lời trên
# LRCLIB (https://lrclib.net), kho lời bài hát mở, API công khai không cần tài khoản.

LYRICS_API = "https://lrclib.net/api"
LYRICS_DELAY = 0.5                 # giây nghỉ trước mỗi request (tự tăng khi server quá tải)
LYRICS_MAX_DURATION_DIFF = 30      # giây: lệch thời lượng tối đa khi khớp "other_version"
LYRICS_USER_AGENT = f"{BOT_NAME}/1.0 (SEG301 course project; https://github.com/dienle25/Music_TMG)"


# ---------------------------------------------------------
# DATABASE & LOG
# ---------------------------------------------------------
# Tên file khác bản 4 website (data/crawler.db) để không ghi đè dữ liệu cũ.

DB_FILE = "data/spotify.db"                    # tính từ thư mục project
SUMMARY_FILE = "data/spotify_summary.txt"
C2C_DB_FILE = "data/c2c_spotify.sqlite"        # schema của đề tài C2C-VN (export_c2c.py)
C2C_REPORT_FILE = "data/c2c_spotify_report.json"
LYRICS_SUMMARY_FILE = "data/lyrics_summary.txt"
RESET_DB = True                                # xoá dữ liệu cũ của spotify.db mỗi lần chạy (trừ bảng lyrics)

VERBOSE = False   # True: in ACCEPT / SKIP cho từng link (Task 7)
