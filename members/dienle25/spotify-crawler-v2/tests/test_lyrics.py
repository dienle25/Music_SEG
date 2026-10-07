"""
Kiểm thử offline cho lyrics.py (LRCLIB giả, không gửi request ra Internet).

Chạy từ thư mục project:
    python -m unittest discover -s tests -v

Lời bài hát trong kiểm thử là chữ giả ("Lời A", "Dòng một"...).
"""

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import urlparse

import requests

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import config  # noqa: E402
import database  # noqa: E402
import lyrics  # noqa: E402
from lyrics import artist_ok, base_title, norm, plain_from_synced  # noqa: E402

BASE = "https://open.spotify.com"


def sid(n):
    return f"L{n}".ljust(22, "x")


TRACKS = [
    # (số, tên bài, ca sĩ, album, thời lượng)
    (1, "Muộn Rồi Mà Sao Còn", ["Sơn Tùng M-TP"], "Muộn Rồi Mà Sao Còn", 276),
    (2, "Em Đã Sai Vì Em Tin", ["Bích Phương"], "Có Khi Nào Rời Xa", 309),
    (3, "Có Đôi Lần - Live", ["Mỹ Tâm"], "Có Đôi Lần (Live)", 264),
    (4, "Nàng Thơ (Beat)", ["Hoàng Dũng"], "Nàng Thơ", 254),
    (5, "Bài Không Có", ["Ca Sĩ Lạ"], "Album Lạ", 200),
    (6, "Bài Của Min", ["MIN"], "Album Min", 210),
    (7, "Hai Ca Sĩ", ["Sơn Tùng M-TP", "Tyga"], "Hai Ca Sĩ", 236),
    (8, "Bài Lỗi Một Lần", ["Đức Phúc"], "Album ĐP", 230),
]


def cand(name, artist, duration, plain=None, synced=None, instrumental=False, cid=1):
    return {"id": cid, "trackName": name, "artistName": artist, "albumName": "x",
            "duration": duration, "instrumental": instrumental,
            "plainLyrics": plain, "syncedLyrics": synced}


SEARCH = {   # (track_name, artist_name) -> kết quả /api/search
    ("Muộn Rồi Mà Sao Còn", "Sơn Tùng M-TP"): [
        cand("Muộn Rồi Mà Sao Còn", "Sơn Tùng M-TP", 127.0, "Lời ngắn", cid=11),
        cand("Muộn Rồi Mà Sao Còn", "Sơn Tùng M-TP", 276.0, "Lời A", cid=12),
        cand("Muộn Rồi Mà Sao Còn", "Sơn Tùng M-TP", 999.0, "Lời dài", cid=13)],
    ("Có Đôi Lần", "Mỹ Tâm"): [
        cand("Có Đôi Lần", "Mỹ Tâm", 250.0, synced="[ar: Mỹ Tâm]\n[00:01.00] Dòng một\n[00:05.20]Dòng hai", cid=31)],
    ("Bài Của Min", "MIN"): [cand("Bài Của Min", "Minh Tuyết", 210.0, "Lời sai ca sĩ", cid=61)],
    ("Hai Ca Sĩ", "Sơn Tùng M-TP"): [cand("Hai Ca Sĩ", "Sơn Tùng M-TP, Tyga", 256.0, "Lời G", cid=71)],
    ("Bài Lỗi Một Lần", "Đức Phúc"): [cand("Bài Lỗi Một Lần", "Đức Phúc", 230.0, "Lời H", cid=81)],
}
GET = {      # track_name -> kết quả /api/get
    "Em Đã Sai Vì Em Tin": cand("Em Đã Sai Vì Em Tin", "Bich Phuong", 309.0, "Lời B", cid=21),
}


def make_response(status, payload):
    response = requests.models.Response()
    response.status_code = status
    response._content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


class FakeLrclib:
    """Session giả: trả lời /api/search và /api/get theo bảng ở trên."""

    def __init__(self, fail_first=(), always_fail=False):
        self.headers = {}
        self.calls = []
        self.fail_first = set(fail_first)      # track_name: lần đầu trả 503
        self.always_fail = always_fail

    def get(self, url, params=None, timeout=None):
        path = urlparse(url).path
        self.calls.append((path, dict(params or {})))
        name = params.get("track_name")
        if self.always_fail:
            return make_response(503, {"message": "busy"})
        if name in self.fail_first:
            self.fail_first.discard(name)
            return make_response(503, {"message": "busy"})
        if path.endswith("/search"):
            return make_response(200, SEARCH.get((name, params.get("artist_name")), []))
        if path.endswith("/get"):
            if name in GET:
                return make_response(200, GET[name])
            return make_response(404, {"code": 404, "name": "TrackNotFound",
                                       "message": "Failed to find specified track"})
        return make_response(404, {})


class HelperTests(unittest.TestCase):

    def test_norm_ignores_accents_case_and_punctuation(self):
        self.assertEqual(norm("Bích Phương"), norm("Bich Phuong"))
        self.assertEqual(norm("Đàm Vĩnh Hưng"), "dam vinh hung")
        self.assertEqual(norm("Sơn Tùng M-TP"), "son tung m tp")

    def test_base_title_removes_version_parts(self):
        self.assertEqual(base_title("Có Đôi Lần - Live"), "Có Đôi Lần")
        self.assertEqual(base_title("Trái Tim Em Cũng Biết Đau - Live in DÀ LAT"), "Trái Tim Em Cũng Biết Đau")
        self.assertEqual(base_title("Mùa Xuân Ơi (feat. Dương Triệu Vũ, Tammy Nguyễn)"), "Mùa Xuân Ơi")
        self.assertEqual(base_title("Come My Way - softer version"), "Come My Way")
        self.assertEqual(base_title("Nơi Này Có Anh (Live Concert Skynote - Hà Nội)"), "Nơi Này Có Anh")
        self.assertEqual(base_title("Chạy Ngay Đi"), "Chạy Ngay Đi")
        self.assertEqual(base_title("Ngày Mai Em Đi (Đen Trắng)"), "Ngày Mai Em Đi (Đen Trắng)")   # không phải phiên bản

    def test_artist_match_uses_whole_words(self):
        self.assertTrue(artist_ok("Sơn Tùng M-TP, Tyga", ["Sơn Tùng M-TP"]))
        self.assertTrue(artist_ok("Bich Phuong", ["Bích Phương"]))
        self.assertFalse(artist_ok("Minh Tuyết", ["MIN"]))
        self.assertFalse(artist_ok("", ["MIN"]))

    def test_synced_lyrics_become_plain_lyrics(self):
        self.assertEqual(plain_from_synced("[ar: X]\n[00:01.00] Dòng một\n[00:05.20]Dòng hai"), "Dòng một\nDòng hai")
        self.assertIsNone(plain_from_synced(None))


class LyricsRunTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {name: getattr(config, name) for name in (
            "LYRICS_SUMMARY_FILE", "MAX_RETRIES", "STOP_AFTER_CONSECUTIVE_FAILURES")}
        self.saved_db = database.DB_PATH
        config.LYRICS_SUMMARY_FILE = os.path.join(self.tmp.name, "lyrics_summary.txt")
        database.DB_PATH = self.db = os.path.join(self.tmp.name, "spotify.db")
        database.create_tables(reset=True)
        for n, title, artists, album, duration in TRACKS:
            url = f"{BASE}/track/{sid(n)}"
            database.save_track({"track_id": sid(n), "url": url, "title": title, "artists": artists,
                                 "artist_ids": [f"A{n}{i}".ljust(22, "x") for i in range(len(artists))],
                                 "artist_text": ", ".join(artists), "album": album, "album_id": None,
                                 "track_number": 1, "release_date": None, "year": None,
                                 "duration_sec": duration, "source_url": None, "html_sha256": None,
                                 "crawled_utc": None})
            database.save_song({"url": url, "title": title, "artist": ", ".join(artists), "album": album,
                                "genre": None, "lyrics": None, "crawled_at": None})
        self.sleeps = []

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(config, name, value)
        database.DB_PATH = self.saved_db
        self.tmp.cleanup()

    def run_lyrics(self, session, **kwargs):
        with redirect_stdout(io.StringIO()):
            return lyrics.run(self.db, sleep=self.sleeps.append, session=session, **kwargs)

    def rows(self):
        conn = sqlite3.connect(self.db)
        try:
            return {track_id: (status, source, match, plain, synced) for track_id, status, source, match, plain, synced
                    in conn.execute("SELECT track_id, status, source, match, plain_lyrics, synced_lyrics FROM lyrics")}
        finally:
            conn.close()

    def song_lyrics(self, n):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute("SELECT lyrics FROM songs WHERE url = ?", (f"{BASE}/track/{sid(n)}",)).fetchone()[0]
        finally:
            conn.close()

    def test_lookup_rules(self):
        session = FakeLrclib(fail_first={"Bài Lỗi Một Lần"})
        finder = self.run_lyrics(session)
        rows = self.rows()

        self.assertEqual(rows[sid(1)][:4], ("found", "lrclib", "search", "Lời A"))        # đúng thời lượng
        self.assertEqual(rows[sid(2)][:4], ("found", "lrclib", "exact", "Lời B"))         # /api/get, ca sĩ không dấu
        self.assertEqual(rows[sid(3)][:4], ("found", "lrclib", "base_title", "Dòng một\nDòng hai"))
        self.assertTrue(rows[sid(3)][4].startswith("[ar: Mỹ Tâm]"))                       # giữ lời có mốc thời gian
        self.assertEqual(rows[sid(4)][:2], ("instrumental", "title"))                     # "(Beat)": không gọi API
        self.assertEqual(rows[sid(5)][0], "not_found")
        self.assertEqual(rows[sid(6)][0], "not_found")                                    # "Minh Tuyết" không phải MIN
        self.assertEqual(rows[sid(7)][:3], ("found", "lrclib", "other_version"))          # lệch 20 giây
        self.assertEqual(rows[sid(8)][:4], ("found", "lrclib", "search", "Lời H"))        # 503 rồi thử lại

        self.assertEqual(self.song_lyrics(1), "Lời A")
        self.assertIsNone(self.song_lyrics(5))
        self.assertFalse([c for c in session.calls if c[1].get("track_name") == "Nàng Thơ (Beat)"])
        self.assertIn(5, self.sleeps)                                                     # backoff 5 giây
        self.assertEqual(finder.retries, 1)
        summary = Path(config.LYRICS_SUMMARY_FILE).read_text(encoding="utf-8")
        self.assertRegex(summary, r"Có lời \(songs\.lyrics\)\s+: 5 \(62%\)")

    def test_second_run_skips_done_tracks_and_retry_flag(self):
        self.run_lyrics(FakeLrclib())
        again = FakeLrclib()
        self.run_lyrics(again)
        self.assertEqual(again.calls, [])                                                 # đã tra hết

        retry = FakeLrclib()
        self.run_lyrics(retry, retry=True)
        names = {params["track_name"] for _, params in retry.calls}
        self.assertEqual(names, {"Bài Không Có", "Bài Của Min"})                         # chỉ bài chưa tìm thấy

    def test_limit(self):
        self.run_lyrics(FakeLrclib(), limit=2)
        self.assertEqual(len(self.rows()), 2)

    def test_stops_when_lrclib_keeps_failing(self):
        config.MAX_RETRIES = 0
        config.STOP_AFTER_CONSECUTIVE_FAILURES = 2
        session = FakeLrclib(always_fail=True)
        finder = self.run_lyrics(session)
        self.assertEqual(self.rows(), {})                  # bài lỗi không được lưu -> lần sau tra lại
        self.assertEqual(finder.stats["error"], 2)         # dừng sau 2 lỗi liên tiếp
        self.assertLess(len(session.calls), 10)
        summary = Path(config.LYRICS_SUMMARY_FILE).read_text(encoding="utf-8")
        self.assertIn("lỗi liên tiếp", summary)

    def test_lyrics_from_another_course_database(self):
        other = os.path.join(self.tmp.name, "nhaccuatui.db")
        conn = sqlite3.connect(other)
        conn.execute("CREATE TABLE songs (id INTEGER PRIMARY KEY, url TEXT, title TEXT, artist TEXT, "
                     "album TEXT, genre TEXT, lyrics TEXT, crawled_at TEXT)")
        conn.execute("INSERT INTO songs (url, title, artist, lyrics) VALUES (?, ?, ?, ?)",
                     ("https://www.nhaccuatui.com/song/abc", "Có Đôi Lần", "Mỹ Tâm", "Lời từ NCT " * 10))
        conn.commit()
        conn.close()

        session = FakeLrclib()
        self.run_lyrics(session, from_db=[other], use_lrclib=False)
        rows = self.rows()
        self.assertEqual(rows[sid(3)][:3], ("found", "db:nhaccuatui.db", "title_artist"))
        self.assertNotIn(sid(1), rows)                     # --no-lrclib: bài khác để lần sau tra LRCLIB
        self.assertEqual(session.calls, [])

    def test_lyrics_table_survives_a_new_crawl(self):
        self.run_lyrics(FakeLrclib())
        database.create_tables(reset=True)                # crawl lại: các bảng khác bị xoá
        self.assertEqual(database.cached_lyrics(sid(1)), "Lời A")
        self.assertIsNone(database.cached_lyrics(sid(5)))


if __name__ == "__main__":
    unittest.main()
