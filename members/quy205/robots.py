# =========================================================
# robots.py - robots.txt checker
# =========================================================

from urllib.robotparser import RobotFileParser
from urllib.parse import urlparse
import requests


class RobotsChecker:
    def __init__(self, session, bot_name, timeout=15):
        self.session = session
        self.bot_name = bot_name
        self.timeout = timeout
        self.cache = {}

    def can_fetch(self, url):
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        if domain in self.cache:
            parser = self.cache[domain]
        else:
            robots_url = f"{parsed.scheme}://{domain}/robots.txt"
            parser = RobotFileParser()
            parser.set_url(robots_url)

            try:
                response = self.session.get(robots_url, timeout=self.timeout)
                if response.status_code == 404:
                    parser.parse([])
                elif response.status_code == 200:
                    parser.parse(response.text.splitlines())
                else:
                    # Fail closed: nếu không đọc được robots.txt thì không crawl.
                    self.cache[domain] = None
                    return False
            except requests.RequestException:
                self.cache[domain] = None
                return False

            self.cache[domain] = parser

        return parser is not None and parser.can_fetch(self.bot_name, url)
