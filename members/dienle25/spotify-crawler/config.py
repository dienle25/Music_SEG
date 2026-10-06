"""Cấu hình tách khỏi thuật toán; không coi URL tìm kiếm là seed đã kiểm chứng."""
from dataclasses import dataclass
from pathlib import Path
import math

TOPIC = 'Vietnamese Music on Spotify'
ROOT = Path(__file__).resolve().parent
SEED_URLS = ()  # SEED URL VALIDATION PENDING: chưa GET HTML vì chính sách Spotify.
CANDIDATE_SEED_URLS = (
    'https://open.spotify.com/album/1AaxmI2e1HRhbwe9XJGPnT',
    'https://open.spotify.com/track/0XaY8eVU9eZO4OdIV6agx1',
)
ALLOWED_DOMAINS = ('open.spotify.com',)
MAX_DEPTH = 2
MAX_PAGES = 30
REQUEST_TIMEOUT = 10
CRAWL_DELAY = 1.0
USER_AGENT = 'SEG301-VietnameseMusicCrawler/1.0'
SECOND_DOMAIN_REQUIRED = True
POLICY_SOURCE = 'https://www.spotify.com/us/legal/user-guidelines/'
POLICY_REVIEWED_AT = '2026-09-22'


@dataclass(frozen=True)
class Config:
    seed_urls: tuple[str, ...] = SEED_URLS
    allowed_domains: tuple[str, ...] = ALLOWED_DOMAINS
    max_depth: int = MAX_DEPTH
    max_pages: int = MAX_PAGES
    request_timeout: float = REQUEST_TIMEOUT
    crawl_delay: float = CRAWL_DELAY
    user_agent: str = USER_AGENT
    db_path: Path = ROOT / 'data' / 'crawler.db'
    max_bytes: int = 2_000_000
    max_requests: int = 90

    def __post_init__(self):
        if not all(math.isfinite(value) for value in (self.request_timeout, self.crawl_delay)):
            raise ValueError('Timeout và delay phải là số hữu hạn.')
        if self.max_depth < 0 or self.max_pages < 1 or self.max_requests < 1:
            raise ValueError('Depth >= 0; pages và requests >= 1.')
        if self.request_timeout <= 0 or self.crawl_delay < 0 or self.max_bytes < 1:
            raise ValueError('Timeout/byte limit phải dương, delay không âm.')
        if not self.allowed_domains or not self.user_agent.strip():
            raise ValueError('Phải khai báo allowed_domains và user_agent.')


def policy_restriction(hostname: str) -> str | None:
    host = hostname.lower().rstrip('.')
    if host == 'spotify.com' or host.endswith('.spotify.com'):
        return ('policy_restricted: Spotify User Guidelines, mục 5, hạn chế crawling/scraping; '
                f'đối chiếu ngày {POLICY_REVIEWED_AT}: {POLICY_SOURCE}')
    return None
