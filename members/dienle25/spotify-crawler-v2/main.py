# =========================================================
# main.py  –  chạy chương trình:  python main.py
# =========================================================
#     python main.py                      # crawl theo config.py
#     python main.py --max-pages 30       # chạy thử nhanh (khoảng 1 phút)
#     python main.py --max-hours 1        # đổi giới hạn thời gian

import argparse

import config
from crawler import Crawler


def parse_args():
    ap = argparse.ArgumentParser(description="Focused web crawler – Spotify")
    ap.add_argument("--max-pages", type=int, help=f"số trang lưu tối đa (mặc định {config.MAX_PAGES})")
    ap.add_argument("--max-hours", type=float, help=f"thời gian chạy tối đa (mặc định {config.MAX_HOURS})")
    args = ap.parse_args()
    if args.max_pages is not None:
        config.MAX_PAGES = args.max_pages
    if args.max_hours is not None:
        config.MAX_HOURS = args.max_hours


def print_config():
    print("=" * 42)
    print("      FOCUSED WEB CRAWLER – SPOTIFY")
    print("=" * 42)
    print()
    print("=" * 10, "CRAWLER CONFIGURATION", "=" * 10)
    print()
    print(f"{'Topic':<16}: {config.TOPIC}")
    print(f"{'Version':<16}: {config.CRAWLER_VERSION}")
    print(f"{'Seed URLs':<16}: {len(config.SEED_URLS)} "
          f"(config.py + {config.SEEDS_FILE})")

    for i, url in enumerate(config.SEED_URLS[:10], start=1):
        print(f"    {i}. {url}")
    if len(config.SEED_URLS) > 10:
        print(f"    ... và {len(config.SEED_URLS) - 10} seed khác")

    print("Allowed Domain  :")
    for host, site in config.DOMAINS.items():
        print(f"    - {host:<22} ({site['name']})")
    print(f"{'Page types':<16}: {', '.join(config.CRAWL_KINDS)}"
          f"{', artist' if config.FETCH_ARTIST_PAGES else ''}")

    print()
    print(f"{'Maximum Depth':<16}: {config.MAX_DEPTH}")
    print(f"{'Maximum Pages':<16}: {config.MAX_PAGES}")
    print(f"{'Maximum Time':<16}: {config.MAX_HOURS} hours")
    print(f"{'Request Timeout':<16}: {config.REQUEST_TIMEOUT} seconds")
    print(f"{'Crawl Delay':<16}: {config.CRAWL_DELAY} second")
    print(f"{'Retry':<16}: HTTP {'/'.join(map(str, config.RETRY_STATUSES))}, "
          f"tối đa {config.MAX_RETRIES} lần, chờ <= {config.MAX_BACKOFF} giây")
    print(f"{'Database':<16}: {config.DB_FILE}")


if __name__ == "__main__":
    parse_args()
    print_config()
    Crawler().run()
