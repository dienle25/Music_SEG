import time
import urllib.robotparser
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

import requests

from config import *
from database import init_db, count, insert
from parser import extract_links, is_song_url, parse_song


class Crawler:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT

        self.q = deque((u, 0) for u in SEED_URLS)
        self.seen = set()
        self.discovered = set()

        self.pages = 0
        self.failed = 0

        self.con = init_db(DB_PATH)
        self.saved = count(self.con)

        self.robots = {}

    def allowed(self, u):
        host = urlparse(u).netloc.lower().split(":")[0]
        return host in ALLOWED_DOMAINS

    def robots_ok(self, u):
        if not RESPECT_ROBOTS:
            return True

        p = urlparse(u)
        base = f"{p.scheme}://{p.netloc}"

        if base not in self.robots:
            rp = urllib.robotparser.RobotFileParser(
                base + "/robots.txt"
            )

            try:
                rp.read()
            except Exception:
                # Không bypass robots khi không thể đọc robots.txt.
                return False

            self.robots[base] = rp

        return self.robots[base].can_fetch(USER_AGENT, u)

    def fetch(self, u):
        err = None

        for i in range(MAX_RETRIES):
            try:
                r = self.s.get(
                    u,
                    timeout=REQUEST_TIMEOUT,
                )
                r.raise_for_status()

                # requests tự đoán encoding; Zing thường dùng UTF-8.
                if not r.encoding:
                    r.encoding = "utf-8"

                return r.text

            except requests.RequestException as e:
                err = e
                time.sleep(1.5 * (i + 1))

        raise err

    def run(self):
        print("=" * 65)
        print("ZINGMP3_PHUQUY CRAWLER")
        print("=" * 65)

        print(
            f"Target: {TARGET_SONGS} | "
            f"Existing: {self.saved} | "
            f"DB: {DB_PATH}\n"
        )

        while (
            self.q
            and self.pages < MAX_PAGES
            and self.saved < TARGET_SONGS
        ):
            u, d = self.q.popleft()

            if (
                u in self.seen
                or d > MAX_DEPTH
                or not self.allowed(u)
            ):
                continue

            if not self.robots_ok(u):
                print("[ROBOTS] skip", u)
                continue

            self.seen.add(u)
            self.pages += 1

            try:
                html = self.fetch(u)

                print(
                    f"[{self.pages:03d}] 200 "
                    f"{u}"
                )

                if is_song_url(u):
                    s = parse_song(html, u)

                    if not s["_valid"]:
                        print(
                            "  - skip: thiếu title "
                            "hoặc thumbnail"
                        )
                        continue

                    if insert(self.con, s):
                        self.saved += 1

                        print(
                            f"  + SONG {self.saved}/"
                            f"{TARGET_SONGS} | "
                            f"{s['title']} | "
                            f"artist={s['artist'] or 'unknown'} | "
                            f"lyric={s['lyric_status']} chars={s.get('lyric_chars', 0)}"
                        )

                else:
                    links = extract_links(html, u)

                    for x in links:
                        if x not in self.discovered:
                            self.discovered.add(x)
                            self.q.append((x, d + 1))

                    print(
                        "  Song links found:",
                        len(links),
                    )

            except Exception as e:
                self.failed += 1

                print(
                    "  ERROR:",
                    type(e).__name__,
                    e,
                )

            time.sleep(CRAWL_DELAY)

        print("\nDONE")
        print("Pages:", self.pages)
        print("Discovered:", len(self.discovered))
        print("Valid songs saved:", self.saved)
        print("Failed:", self.failed)
        print(
            "Database:",
            Path(DB_PATH).resolve(),
        )


if __name__ == "__main__":
    Crawler().run()
