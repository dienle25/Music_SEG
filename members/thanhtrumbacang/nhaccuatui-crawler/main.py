from config import (
    ALLOWED_DOMAINS,
    DB_PATH,
    MAX_DEPTH,
    MAX_PAGES,
    REQUEST_TIMEOUT,
    CRAWL_DELAY,
    SEED_URLS,
    TOPIC,
)
from crawler import FocusedMusicCrawler


def print_config():
    print("=" * 55)
    print("          FOCUSED WEB CRAWLER - MUSIC")
    print("=" * 55)
    print(f"Topic            : {TOPIC}")
    print("Seed URLs:")
    for i, url in enumerate(SEED_URLS, 1):
        print(f"  {i}. {url}")

    print("Allowed Domains:")
    for domain in ALLOWED_DOMAINS:
        print(f"  - {domain}")

    print(f"Maximum Pages    : {MAX_PAGES}")
    print(f"Maximum Depth    : {MAX_DEPTH}")
    print(f"Request Timeout  : {REQUEST_TIMEOUT} seconds")
    print(f"Crawl Delay      : {CRAWL_DELAY} second(s)")
    print(f"Database         : {DB_PATH}")
    print("=" * 55)


def main():
    print_config()

    crawler = FocusedMusicCrawler(SEED_URLS, DB_PATH)

    try:
        crawler.run()
    finally:
        crawler.close()


if __name__ == "__main__":
    main()
