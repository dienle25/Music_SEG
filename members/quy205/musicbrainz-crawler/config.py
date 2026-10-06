# ============================================================
# Focused Web Crawler - Configuration
# ============================================================

TOPIC = "Music"

# The assignment document asks for at least 2 domains.
# NOTE: "Music" is not one of the example topics in the supplied
# assignment table. Confirm with your instructor that Music is allowed.
SEED_URLS = [
    "https://musicbrainz.org/",
    "https://freemusicarchive.org/",
]

ALLOWED_DOMAINS = [
    "musicbrainz.org",
    "freemusicarchive.org",
]

MAX_DEPTH = 3
MAX_PAGES = 100
REQUEST_TIMEOUT = 10

# MusicBrainz robots.txt specifies Crawl-delay: 2.
# We use 2 seconds globally to keep the crawler conservative.
CRAWL_DELAY = 2.0

USER_AGENT = "SEG301-FocusedMusicCrawler/1.0 (+student-assignment)"

DATABASE_PATH = "data/crawler.db"

# Non-HTML resources that should not be crawled.
BLOCKED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".json", ".xml", ".zip", ".rar", ".7z",
    ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".webm", ".avi",
    ".mov", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
}

BLOCKED_SCHEMES = {
    "mailto", "tel", "javascript", "data", "file",
}
