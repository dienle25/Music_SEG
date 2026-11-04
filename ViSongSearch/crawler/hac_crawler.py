"""Polite, resumable crawler for Hợp Âm Chuẩn song pages (SEG 3: crawls and feeds).

It visits song pages by ID in a fixed random order, so the crawl is a uniform
sample of the catalogue and can stop and resume at any point. The lyric
extraction follows the group's validated parser (members/quy205/.../parser.py):
lyrics are the `.hopamchuan_lyric` spans inside each `.chord_lyric_line`, so
chords never enter the text.

Usage
    python crawler/hac_crawler.py --db data/hac.sqlite --max-songs 15000 --hours 1.9
    python crawler/hac_crawler.py --db data/hac.sqlite --export data/hac.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.robotparser
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://hopamchuan.com"
USER_AGENT = ("ViSongSearch-StudentCrawler/1.0 (+https://github.com/dienle25/Music_SEG; "
              "educational research, 1 request/second)")
SCHEMA = """
create table if not exists fetched(id integer primary key, status integer, final_url text,
                                   fetched_at text, note text);
create table if not exists songs(id integer primary key, url text, title text, composer text,
                                 singers text, genre text, rhythm text, views integer,
                                 lyrics text, html_sha256 text, crawled_at text,
                                 likes integer, updated text);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "").replace("\xa0", " ").strip()


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", nfc(s)).strip()


# ------------------------------------------------------------------ parsing

def labeled_value(soup: BeautifulSoup, label: str):
    """The cell next to a label such as 'Tác giả' or 'Thể loại' (same idea as the group parser)."""
    wanted = label.casefold()
    for node in soup.find_all(string=True):
        if clean(str(node)).rstrip(":： ").casefold() != wanted:
            continue
        row = node.parent
        for _ in range(5):
            if row is None:
                break
            if row.name in {"tr", "dl"}:
                cells = row.find_all(["td", "th", "dd", "dt"], recursive=False)
                for i, cell in enumerate(cells):
                    if clean(cell.get_text(" ", strip=True)).rstrip(":： ").casefold() == wanted and i + 1 < len(cells):
                        return cells[i + 1]
            row = row.parent
        # Fallback: label and value inside the same small block ("Tác giả: Trịnh Công Sơn")
        parent = node.parent
        if parent is not None:
            links = [clean(a.get_text(" ", strip=True)) for a in parent.find_all("a")]
            if links:
                return parent
    return None


def names_in(cell) -> list[str]:
    if cell is None:
        return []
    out = []
    for a in cell.find_all("a"):
        name = clean(a.get_text(" ", strip=True))
        if name and name.casefold() not in {"xem thêm", "xem tất cả", "n/a"} and name not in out:
            out.append(name)
    if not out:
        txt = clean(cell.get_text(" ", strip=True))
        if txt and txt.casefold() not in {"n/a", "không rõ"}:
            out = [t.strip() for t in re.split(r",|;", txt) if t.strip()]
    return out


def extract_lyrics(soup: BeautifulSoup) -> str:
    root = soup.select_one("#song-lyric-pre, #song-lyric, .hopamchuan_lyric")
    if root is None:
        return ""
    lines = []
    line_nodes = root.select(".chord_lyric_line")
    if line_nodes:
        for ln in line_nodes:
            text = "".join(s.get_text("", strip=False) for s in ln.select(".hopamchuan_lyric"))
            text = re.sub(r"[ \t]+", " ", text.replace("\xa0", " ")).strip()
            if text:
                lines.append(text)
            elif "empty_line" in (ln.get("class") or []) and lines and lines[-1] != "":
                lines.append("")
    else:
        spans = root.select(".hopamchuan_lyric") or [root]
        lines = [l.strip() for l in "".join(s.get_text("", strip=False) for s in spans).splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\[[A-G][^\]\n]{0,18}\]", "", text)      # chords that slipped in
    return unicodedata.normalize("NFC", text).strip()


def extract_title(soup: BeautifulSoup) -> str:
    for sel in ("h1.song-title", ".song-title h1", "h1[itemprop='name']", "main h1", "article h1", "h1"):
        node = soup.select_one(sel)
        if node:
            t = clean(node.get_text(" ", strip=True))
            t = re.sub(r"\s*[-|–—:]\s*(?:Hợp Âm Chuẩn|Hop Am Chuan).*$", "", t, flags=re.I)
            if t:
                return t
    og = soup.find("meta", attrs={"property": "og:title"})
    t = clean(og["content"]) if og and og.get("content") else ""
    t = re.sub(r"^Hợp âm\s+", "", t, flags=re.I)
    return re.sub(r"\s*[-|–—]\s*(?:Hợp Âm Chuẩn).*$", "", t, flags=re.I)


def labeled_number(soup: BeautifulSoup, label: str) -> int | None:
    """Integer in the info-table cell after a label, e.g. 'Lượt xem:' -> 1,211,247."""
    cell = labeled_value(soup, label)
    if cell is None:
        return None
    m = re.search(r"\d[\d.,]*", cell.get_text(" ", strip=True))
    return int(re.sub(r"[.,]", "", m.group(0))) if m else None


def extract_views(soup: BeautifulSoup) -> int | None:
    return labeled_number(soup, "Lượt xem")


def extract_singers(soup: BeautifulSoup) -> list[str]:
    """Performers listed under the title (one entry per singer version of the song)."""
    out = []
    for a in soup.select("#song-author .perform-singer-list a.author-item"):
        name = clean(a.get_text(" ", strip=True))
        if name and name.casefold() not in {"n/a", "không rõ"} and name not in out:
            out.append(name)
    return out


def extract_rhythm(soup: BeautifulSoup) -> str:
    node = soup.select_one("#display-rhythm")
    if node is None:
        return ""
    t = clean(node.get_text(" ", strip=True))
    return re.sub(r"^Điệu\s+", "", t, flags=re.I)


def parse_song(html: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    lyrics = extract_lyrics(soup)
    title = extract_title(soup)
    if not title or len(lyrics) < 30:
        return None
    return {
        "url": url,
        "title": title,
        "composer": ", ".join(names_in(labeled_value(soup, "Tác giả"))),
        "singers": ", ".join(extract_singers(soup) or names_in(labeled_value(soup, "Ca sĩ"))),
        "genre": ", ".join(names_in(labeled_value(soup, "Thể loại"))),
        "rhythm": extract_rhythm(soup),
        "views": extract_views(soup),
        "likes": labeled_number(soup, "Yêu thích"),
        "updated": clean(labeled_value(soup, "Cập nhật").get_text(" ", strip=True)) if labeled_value(soup, "Cập nhật") else "",
        "lyrics": lyrics,
        "html_sha256": hashlib.sha256(html.encode("utf-8", "ignore")).hexdigest(),
    }


# ------------------------------------------------------------------ crawling

class Crawler:
    def __init__(self, db: str, delay: float = 1.0):
        self.con = sqlite3.connect(db)
        self.con.executescript(SCHEMA)
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "vi-VN,vi;q=0.9"})
        self.robots = urllib.robotparser.RobotFileParser()
        r = self.http.get(BASE + "/robots.txt", timeout=20)
        self.robots.parse(r.text.splitlines() if r.ok else [])
        crawl_delay = self.robots.crawl_delay(USER_AGENT) or self.robots.crawl_delay("*") or 0
        self.delay = max(delay, float(crawl_delay))
        self.refused = 0          # consecutive 403/429/failed answers
        print(f"robots.txt status={r.status_code}, delay={self.delay}s", flush=True)

    def done_ids(self) -> set[int]:
        return {r[0] for r in self.con.execute("select id from fetched")}

    def n_songs(self) -> int:
        return self.con.execute("select count(*) from songs").fetchone()[0]

    def fetch(self, song_id: int) -> None:
        url = f"{BASE}/song/{song_id}/x/"
        if not self.robots.can_fetch(USER_AGENT, url):
            self.con.execute("insert or replace into fetched values(?,?,?,?,?)", (song_id, -1, url, now(), "robots"))
            return
        wait = self.delay
        for attempt in range(5):
            try:
                resp = self.http.get(url, timeout=30, allow_redirects=True)
            except requests.RequestException as e:
                print(f"  {song_id}: {e}; retry in {wait * 4:.0f}s", flush=True)
                time.sleep(wait * 4)
                wait *= 2
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                retry_after = resp.headers.get("Retry-After")
                pause = float(retry_after) if retry_after and retry_after.isdigit() else wait * 8
                print(f"  {song_id}: HTTP {resp.status_code}; pause {pause:.0f}s", flush=True)
                time.sleep(pause)
                wait *= 2
                continue
            break
        else:
            self.con.execute("insert or replace into fetched values(?,?,?,?,?)", (song_id, 0, url, now(), "failed"))
            self.refused += 1
            return
        self.refused = self.refused + 1 if resp.status_code in (403, 429) else 0
        note = ""
        if resp.status_code == 200 and "/song/" in resp.url:
            song = parse_song(resp.text, resp.url)
            if song:
                self.con.execute(
                    "insert or replace into songs values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (song_id, song["url"], song["title"], song["composer"], song["singers"], song["genre"],
                     song["rhythm"], song["views"], song["lyrics"], song["html_sha256"], now(),
                     song["likes"], song["updated"]))
                note = "song"
            else:
                note = "no_lyrics"
        self.con.execute("insert or replace into fetched values(?,?,?,?,?)",
                         (song_id, resp.status_code, resp.url, now(), note))

    def run(self, id_min: int, id_max: int, seed: int, max_songs: int, hours: float) -> None:
        ids = list(range(id_min, id_max + 1))
        random.Random(seed).shuffle(ids)
        done = self.done_ids()
        todo = [i for i in ids if i not in done]
        start = time.time()
        print(f"todo={len(todo)} done={len(done)} songs={self.n_songs()}", flush=True)
        for k, song_id in enumerate(todo):
            if self.n_songs() >= max_songs or time.time() - start > hours * 3600:
                break
            t0 = time.time()
            self.fetch(song_id)
            if self.refused >= 10:
                self.con.commit()
                print(f"[{now()}] BLOCKED: 10 refusals in a row (403/429); stopping. songs={self.n_songs()}", flush=True)
                return 3
            if k % 20 == 0:
                self.con.commit()
            if k % 200 == 0:
                rate = (k + 1) / max(1e-9, time.time() - start)
                print(f"[{now()}] fetched {k + 1} this run, songs={self.n_songs()}, {rate:.2f} pages/s", flush=True)
            time.sleep(max(0.0, self.delay - (time.time() - t0)))
        self.con.commit()
        print(f"stopped: songs={self.n_songs()}", flush=True)
        return 0

    def export(self, path: str) -> int:
        n = 0
        with open(path, "w", encoding="utf-8") as f:
            for row in self.con.execute("select id,url,title,composer,singers,genre,rhythm,views,lyrics,html_sha256,"
                                        "likes,updated from songs order by id"):
                sid, url, title, composer, singers, genre, rhythm, views, lyrics, sha, likes, updated = row
                f.write(json.dumps({"doc_id": f"hac{sid}", "url": url, "source": "hopamchuan.com",
                                    "title": title, "artist": composer or "", "genre": genre or "",
                                    "lyrics": lyrics, "singers": singers or "", "rhythm": rhythm or "",
                                    "views": views, "likes": likes, "updated": updated or "",
                                    "html_sha256": sha}, ensure_ascii=False) + "\n")
                n += 1
        return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/hac.sqlite")
    ap.add_argument("--id-min", type=int, default=1)
    ap.add_argument("--id-max", type=int, default=90000)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--max-songs", type=int, default=15000)
    ap.add_argument("--hours", type=float, default=1.9, help="stop after this many hours (resume later)")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--export", help="write songs to this JSONL file and exit")
    ap.add_argument("--test", type=int, nargs="*", help="fetch and print these IDs without saving")
    a = ap.parse_args(argv)
    if a.test:
        http = requests.Session()
        http.headers["User-Agent"] = USER_AGENT
        for sid in a.test:
            r = http.get(f"{BASE}/song/{sid}/x/", timeout=30)
            song = parse_song(r.text, r.url) if r.ok else None
            print(sid, r.status_code, r.url)
            print(json.dumps(song, ensure_ascii=False, indent=1)[:1500] if song else "  (no song parsed)")
            time.sleep(1)
        return
    c = Crawler(a.db, a.delay)
    if a.export:
        print("exported", c.export(a.export))
        return
    return c.run(a.id_min, a.id_max, a.seed, a.max_songs, a.hours)


if __name__ == "__main__":
    sys.exit(main())
