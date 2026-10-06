# Cấu hình Focused Web Crawler - Chủ đề: Âm nhạc

TOPIC = "Music"

# Chỉ crawl NhacCuaTui theo yêu cầu.
SEED_URLS = [
    "https://www.nhaccuatui.com/",
]

ALLOWED_DOMAINS = [
    "nhaccuatui.com",
]

MAX_DEPTH = 2
MAX_PAGES = 50
REQUEST_TIMEOUT = 10
CRAWL_DELAY = 1.0
USER_AGENT = "FocusedMusicCrawler/1.0 (educational assignment)"

DB_PATH = "data/crawler.db"

IGNORED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
    ".css", ".js", ".zip", ".rar", ".7z",
    ".mp3", ".wav", ".flac", ".mp4", ".avi", ".mkv",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
}

IGNORED_SCHEMES = {"mailto", "javascript", "tel", "data"}
