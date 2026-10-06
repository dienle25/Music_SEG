"""
check_domain.py - Helper script (run BEFORE crawling)

Checks, for each seed URL in config.py:
  1. robots.txt content and whether the seed is allowed for our User-Agent
  2. HTTP status of the home page
  3. HTML size and number of <a href> links (tells if the page needs JavaScript)

Usage:
    python check_domain.py
"""

from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

import config


def check(seed):
    parsed = urlparse(seed)
    base = f"{parsed.scheme}://{parsed.netloc}"
    headers = {"User-Agent": config.USER_AGENT}

    print("=" * 60)
    print("Seed:", seed)

    # ---- robots.txt ----
    robots_url = base + "/robots.txt"
    try:
        r = requests.get(robots_url, headers=headers, timeout=config.REQUEST_TIMEOUT)
        print(f"robots.txt   : HTTP {r.status_code}")
        if r.status_code == 200:
            rp = RobotFileParser()
            rp.parse(r.text.splitlines())
            print("can_fetch '/':", rp.can_fetch(config.USER_AGENT, seed))
            print("crawl_delay  :", rp.crawl_delay(config.USER_AGENT))
            print("--- first lines of robots.txt ---")
            for line in r.text.splitlines()[:25]:
                print("   ", line)
            print("---------------------------------")
        else:
            print("No usable robots.txt (crawler will treat 404 as allowed, 403 as blocked)")
    except requests.RequestException as e:
        print("robots.txt   : ERROR", e)

    # ---- home page ----
    try:
        r = requests.get(seed, headers=headers, timeout=config.REQUEST_TIMEOUT)
        print("HTTP status  :", r.status_code)
        print("Content-Type :", r.headers.get("Content-Type"))
        print("HTML length  :", len(r.text))
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.find_all("a", href=True)
        print("Links <a>    :", len(links))
        print("Title        :", soup.title.get_text(strip=True) if soup.title else None)
        print("Sample links :")
        for a in links[:8]:
            print("   ", a["href"])

        if r.status_code != 200:
            print(">>> RESULT: NOT OK (status is not 200) -> choose another domain")
        elif len(links) < 20:
            print(">>> RESULT: WARNING (very few links; page may need JavaScript)")
        else:
            print(">>> RESULT: OK to crawl (still respect robots.txt)")
    except requests.RequestException as e:
        print("Request ERROR:", e)


if __name__ == "__main__":
    for seed in config.SEED_URLS:
        check(seed)
