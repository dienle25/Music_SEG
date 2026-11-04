"""Crawl statistics for the paper, read from the crawler's SQLite database.

    python3 scripts/crawl_stats.py data/hac.sqlite work_full [--id-max 90000]

Writes WORK/results/crawl_stats.json: pages fetched, songs with lyrics, answers by HTTP status,
pages skipped by robots.txt, failures and the first and last fetch dates.
"""
import argparse
import json
import os
import sqlite3
from collections import Counter


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("work")
    ap.add_argument("--id-max", type=int, default=90000, help="largest identifier the crawler could visit")
    a = ap.parse_args(argv)
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rows = con.execute("select status, note, fetched_at from fetched").fetchall()
    songs = con.execute("select count(*) from songs").fetchone()[0]
    status = Counter(str(r[0]) for r in rows)
    notes = Counter(r[1] or "" for r in rows)
    dates = sorted(r[2] for r in rows if r[2])
    out = {"pages_fetched": sum(1 for r in rows if r[0] not in (-1,)), "ids_visited": len(rows), "songs": songs,
           "status": dict(status), "robots_skipped": notes.get("robots", 0), "errors": notes.get("failed", 0),
           "no_lyrics": notes.get("no_lyrics", 0), "start": dates[0][:10] if dates else None,
           "end": dates[-1][:10] if dates else None, "id_max": a.id_max}
    os.makedirs(os.path.join(a.work, "results"), exist_ok=True)
    json.dump(out, open(os.path.join(a.work, "results", "crawl_stats.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
