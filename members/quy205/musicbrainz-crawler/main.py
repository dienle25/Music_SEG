from config import (
    ALLOWED_DOMAINS,
    CRAWL_DELAY,
    DATABASE_PATH,
    MAX_DEPTH,
    MAX_PAGES,
    REQUEST_TIMEOUT,
    SEED_URLS,
    TOPIC,
)
from crawler import FocusedCrawler


def print_configuration():
    print("=" * 63)
    print("                 FOCUSED WEB CRAWLER")
    print("=" * 63)
    print(f"Topic           : {TOPIC}")
    print(f"Seed URLs       : {len(SEED_URLS)}")

    for index, url in enumerate(SEED_URLS, start=1):
        print(f"  {index}. {url}")

    print("Allowed Domains :")
    for domain in ALLOWED_DOMAINS:
        print(f"  - {domain}")

    print(f"Maximum Depth   : {MAX_DEPTH}")
    print(f"Maximum Pages   : {MAX_PAGES}")
    print(f"Request Timeout : {REQUEST_TIMEOUT} seconds")
    print(f"Crawl Delay     : {CRAWL_DELAY} second(s)")
    print("=" * 63)
    print()


def print_summary(stats, frontier_size):
    print()
    print("=" * 63)
    print("                 CRAWLING SUMMARY")
    print("=" * 63)
    print(f"Topic                  : {TOPIC}")
    print(f"Seed URLs              : {len(SEED_URLS)}")
    print(f"Pages Crawled          : {stats['pages_crawled']}")
    print(f"Unique URLs Discovered : {stats['unique_urls_discovered']}")
    print(f"Skipped URLs           : {stats['skipped_urls']}")
    print(f"Failed Requests        : {stats['failed_requests']}")
    print(f"Maximum Depth          : {MAX_DEPTH}")

    for depth in range(MAX_DEPTH + 1):
        count = stats["depth_counts"].get(depth, 0)
        print(f"Depth {depth:<15}: {count} pages")

    for status_code in sorted(stats["status_counts"]):
        print(
            f"HTTP {status_code:<17}: "
            f"{stats['status_counts'][status_code]}"
        )

    print(f"Frontier Remaining     : {frontier_size}")
    print("=" * 63)


def main():
    print_configuration()

    crawler = FocusedCrawler(
        seed_urls=SEED_URLS,
        db_path=DATABASE_PATH,
    )

    try:
        stats = crawler.crawl()
        print_summary(stats, crawler.frontier.size())
    finally:
        crawler.close()


if __name__ == "__main__":
    main()
