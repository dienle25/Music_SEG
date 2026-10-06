TOPIC = 'Music'

SEED_URLS = [
    'https://www.nhaccuatui.com/chart/1-1-d9-2026'
]

ALLOWED_DOMAINS = [
    'nhaccuatui.com'
]

MAX_DEPTH = 3
MAX_PAGES = 350

REQUEST_TIMEOUT = 15
CRAWL_DELAY = 1.0

USER_AGENT = 'MusicCrawlerEducational/1.0'

DB_PATH = 'data/music.db'

RESPECT_ROBOTS = True

IGNORED_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.svg',
    '.webp', '.css', '.js',
    '.mp3', '.wav', '.flac',
    '.mp4', '.avi', '.mkv',
    '.pdf', '.zip', '.rar'
}