# ZING MP3 PHUQUY CRAWLER

SEED_URLS = [
    "https://zingmp3.vn/top100",
    "https://zingmp3.vn/zing-chart",
    "https://zingmp3.vn/moi-phat-hanh",
]

ALLOWED_DOMAINS = {
    "zingmp3.vn",
    "www.zingmp3.vn",
}

TARGET_SONGS = 300
MAX_PAGES = 1500
MAX_DEPTH = 3
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
CRAWL_DELAY = 1.0

USER_AGENT = "zingmp3_phuquy/1.0 (educational crawler)"

DB_PATH = "data/music.db"

# Tôn trọng robots.txt.
RESPECT_ROBOTS = True
