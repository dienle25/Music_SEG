# =========================================================
# main.py - chạy hopamchuan_phuquy
# =========================================================

import config
from crawler import Crawler


def print_config():
    print("=" * 60)
    print("                 hopamchuan_phuquy")
    print("        HopAmChuan Focused Music Crawler")
    print("=" * 60)
    print(f"Target songs       : {config.MAX_SONGS}")
    print(f"Maximum depth      : {config.MAX_DEPTH}")
    print(f"Crawl delay        : {config.CRAWL_DELAY}s")
    print(f"Require lyrics     : {config.REQUIRE_LYRICS}")
    print(f"Min lyrics chars   : {config.MIN_LYRICS_CHARS}")
    print(f"Require thumbnail  : {config.REQUIRE_THUMBNAIL}")
    print(f"Database           : {config.DB_FILE}")
    print("\nSeeds:")
    for i, seed in enumerate(config.SEED_URLS, 1):
        print(f"  {i}. {seed}")
    print()


if __name__ == "__main__":
    print_config()
    Crawler().run()
