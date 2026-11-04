"""Per-song release file: what lets others rebuild and check the collection without the lyrics.

    python3 -I scripts/make_release.py work_full [--course-root ../dienle25/music_seg]

Writes WORK/release/songs.tsv with one row per indexed song:
  doc_id, url, source, crawl_date, cluster, split, n_syllables, lyrics_sha256
lyrics_sha256 is the SHA-256 of the song's syllables (visong.text.syllables: NFC, lowercase, no
punctuation) joined by single spaces, so a re-crawl can be compared song by song with ours. The
crawl date is the day of the uniform crawl for its songs and the crawled_at value of the course
database for pilot songs (empty when that database kept none).
"""
import argparse
import ast
import hashlib
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong.text import syllables                                                 # noqa: E402


def course_dates(db_path):
    """url -> crawl date (YYYY-MM-DD) from a course crawler database, if it records one."""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cols = [r[1] for r in con.execute("PRAGMA table_info(songs)")]
        if "crawled_at" not in cols:
            return {}
        return {u: (d or "")[:10] for u, d in con.execute("SELECT url, crawled_at FROM songs")}
    except sqlite3.Error:
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--course-root", default="../dienle25/music_seg",
                    help="folder that the 'db' paths of the pilot songs are relative to")
    a = ap.parse_args(argv)
    crawl = json.load(open(os.path.join(a.work, "results", "crawl_stats.json")))
    origin_dbs, rows = {}, []
    with open(os.path.join(a.work, "corpus.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            extra = r.get("extra") or {}
            if isinstance(extra, str):
                extra = ast.literal_eval(extra)
            date = ""
            if r["doc_id"].startswith("hac") and "db" not in extra:
                date = crawl.get("end", "")
            elif "db" in extra:
                db = os.path.normpath(os.path.join(a.course_root, "..", extra["db"].lstrip("./").split("/", 1)[1])) \
                    if extra["db"].startswith("../") else extra["db"]
                if db not in origin_dbs:
                    origin_dbs[db] = course_dates(db) if os.path.exists(db) else {}
                date = origin_dbs[db].get(r["url"], "")
            syl = syllables(r["lyrics"])
            rows.append([r["doc_id"], r["url"], r["source"], date, extra.get("cluster", ""), extra.get("split", ""),
                         str(len(syl)), hashlib.sha256(" ".join(syl).encode("utf-8")).hexdigest()])
    out_dir = os.path.join(a.work, "release")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "songs.tsv"), "w", encoding="utf-8") as f:
        f.write("doc_id\turl\tsource\tcrawl_date\tcluster\tsplit\tn_syllables\tlyrics_sha256\n")
        for row in rows:
            f.write("\t".join(x.replace("\t", " ") for x in row) + "\n")
    dated = sum(bool(r[3]) for r in rows)
    print(f"{len(rows)} songs, {dated} with a crawl date, course databases read: "
          f"{sum(bool(v) for v in origin_dbs.values())}/{len(origin_dbs)}")


if __name__ == "__main__":
    main()
