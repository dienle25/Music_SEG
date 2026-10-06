"""
Small connectivity test before running the complete crawler.

Run:
    python test_sites.py
"""

import requests
from bs4 import BeautifulSoup

from config import SEED_URLS, REQUEST_TIMEOUT, USER_AGENT


def test_url(url):
    print("=" * 63)
    print(f"Testing: {url}")
    print("=" * 63)

    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )

        print("HTTP Status Code:", response.status_code)

        soup = BeautifulSoup(response.text, "html.parser")

        print("\nPage title:")
        if soup.title:
            print(soup.title.get_text(" ", strip=True))
        else:
            print("(no title)")

        print("\nPage text (first 2000 characters):")
        print(soup.get_text(" ", strip=True)[:2000])

        links = soup.find_all("a", href=True)
        print("\nNumber of <a> tags:", len(links))

        print("\nFirst 20 links:")
        for i, link in enumerate(links[:20], start=1):
            print(f"{i}. Text:")
            print("  ", link.get_text(" ", strip=True)[:100])
            print("   URL:", link["href"])

        print()

    except requests.RequestException as exc:
        print("Request failed:", exc)
        print()


if __name__ == "__main__":
    for seed in SEED_URLS:
        test_url(seed)
