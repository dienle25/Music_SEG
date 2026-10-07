# =========================================================
# config.py - hopamchuan_phuquy | Focused Crawler cho HopAmChuan
# =========================================================

PROJECT_NAME = "hopamchuan_phuquy"
TOPIC = "Music - HopAmChuan"

BASE_URL = "https://hopamchuan.com"
DOMAIN = "hopamchuan.com"

# Các trang điệu có danh sách bài hát. offset tăng 10 bài/trang.
SEED_URLS = [
    "https://hopamchuan.com/rhythm/v/ballad",
    "https://hopamchuan.com/rhythm/v/pop",
    "https://hopamchuan.com/rhythm/v/slow",
    "https://hopamchuan.com/rhythm/v/rock",
    "https://hopamchuan.com/rhythm/v/blues",
]

MAX_SONGS = 200
MAX_DISCOVERED_URLS = 8000
MAX_DEPTH = 3
REQUEST_TIMEOUT = 20
CRAWL_DELAY = 1.5

# Chỉ nhận bài đạt đủ quality gate.
REQUIRE_LYRICS = True
MIN_LYRICS_CHARS = 30
REQUIRE_THUMBNAIL = True

DB_FILE = "data/hopamchuan_phuquy.db"
SUMMARY_FILE = "data/crawl_summary.txt"
RESET_DB = True
VERBOSE = True

BOT_NAME = "hopamchuan_phuquy-StudentCrawler/1.0"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/154.0 Safari/537.36 "
    "(compatible; hopamchuan_phuquy-StudentCrawler/1.0)"
)

BLOCKED_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".json", ".xml", ".zip", ".rar", ".pdf",
    ".mp3", ".mp4", ".m4a", ".flac", ".wav", ".m3u8",
)

# Trang bài hát thực tế có dạng /song/8889/la-lung/
SONG_URL_PATTERNS = [
    r"^/song/\d+/[^/?#]+/?$",
]

DISCOVERY_PATTERNS = [
    r"^/rhythm(?:/.*)?$",
    r"^/genre(?:/.*)?$",
    r"^/playlist(?:/.*)?$",
    r"^/song/?$",
]
