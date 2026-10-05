# =========================================================
# main.py  –  chạy chương trình:  python main.py
# =========================================================

import config
from crawler import Crawler


def print_config():
    print("=" * 42)
    print("          FOCUSED WEB CRAWLER")
    print("=" * 42)
    print()
    print("=" * 10, "CRAWLER CONFIGURATION", "=" * 10)
    print()
    print(f"{'Topic':<16}: {config.TOPIC}")
    print(f"{'Seed URLs':<16}: {len(config.SEED_URLS)}")

    for i, url in enumerate(config.SEED_URLS, start=1):
        print(f"    {i}. {url}")

    print("Allowed Domains:")
    for host, site in config.DOMAINS.items():
        print(f"    - {host:<22} ({site['name']})")

    print()
    print(f"{'Maximum Depth':<16}: {config.MAX_DEPTH}")
    print(f"{'Maximum Pages':<16}: {config.MAX_PAGES} "
          f"(tối đa {config.MAX_PAGES_PER_DOMAIN} / domain)")
    print(f"{'Request Timeout':<16}: {config.REQUEST_TIMEOUT} seconds")
    print(f"{'Crawl Delay':<16}: {config.CRAWL_DELAY} second")
    print(f"{'Sitemap feeds':<16}: {'ON' if config.USE_SITEMAPS else 'OFF'} "
          f"({config.SITEMAP_URL_LIMIT} URL / sitemap)")


if __name__ == "__main__":
    print_config()
    Crawler().run()
