"""
main.py - Entry point of the focused web crawler.

Usage:
    python main.py
"""

from crawler import Crawler


def main():
    crawler = Crawler(reset_db=True)
    crawler.run()


if __name__ == "__main__":
    main()
