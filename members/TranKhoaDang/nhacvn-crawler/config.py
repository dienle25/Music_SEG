"""
config.py - Crawler configuration (Task 1)

Change the parameters here without touching the crawler logic.
NOTE: Before running, verify robots.txt / accessibility of each domain.
If a site blocks crawlers (HTTP 403), replace it with another Music domain.
"""

# ---------------------------------------------------------------
# Topic and domains
# ---------------------------------------------------------------
TOPIC = "Music"

SEED_URLS = [
    "https://nhac.vn/",
]

ALLOWED_DOMAINS = [
    "nhac.vn",
]

# Domain rule: a URL is allowed if its host equals an allowed domain
# or is a subdomain of it (www.example.com and sub.example.com
# both match "example.com").


# ---------------------------------------------------------------
# Crawling limits
# ---------------------------------------------------------------
MAX_DEPTH = 3
MAX_PAGES = 300
REQUEST_TIMEOUT = 10   # seconds
CRAWL_DELAY = 1        # seconds between two requests (politeness)

# ---------------------------------------------------------------
# Behaviour switches
# ---------------------------------------------------------------
# True  -> download and obey robots.txt
# False -> skip robots.txt checking
RESPECT_ROBOTS = True

# True  -> also store song lyrics (songs.lyrics and pages.content)
# False -> lyric blocks are removed before saving
# Educational use only: do not publish or redistribute the database.
STORE_LYRICS = True

# ---------------------------------------------------------------
# HTTP settings
# ---------------------------------------------------------------
USER_AGENT = "Mozilla/5.0 (compatible; MusicStudentCrawler/1.0; educational project)"

# ---------------------------------------------------------------
# URL filtering
# ---------------------------------------------------------------
IGNORED_SCHEMES = ("mailto", "javascript", "tel", "ftp", "sms", "data")

IGNORED_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".css", ".js", ".json", ".xml",
    ".zip", ".rar", ".gz", ".tar", ".7z",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv",
    ".woff", ".woff2", ".ttf", ".eot",
)

# ---------------------------------------------------------------
# Storage
# ---------------------------------------------------------------
DB_PATH = "data/crawler.db"

# Store at most this many characters of page text (keeps DB small)
MAX_CONTENT_LENGTH = 20000


def print_config():
    """Display the crawling configuration."""
    print("========== CRAWLER CONFIGURATION ==========")
    print(f"Topic          : {TOPIC}")
    print(f"Seed URLs      : {len(SEED_URLS)}")
    for u in SEED_URLS:
        print(f"  - {u}")
    print("Allowed Domains:")
    for d in ALLOWED_DOMAINS:
        print(f"  - {d}")
    print(f"Maximum Depth  : {MAX_DEPTH}")
    print(f"Maximum Pages  : {MAX_PAGES}")
    print(f"Request Timeout: {REQUEST_TIMEOUT} seconds")
    print(f"Crawl Delay    : {CRAWL_DELAY} second(s)")
    print(f"Respect robots : {RESPECT_ROBOTS}")
    print(f"Store lyrics   : {STORE_LYRICS}")
    print("===========================================")
