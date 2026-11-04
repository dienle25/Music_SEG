"""Remove dev/test queries whose target cluster holds a song that NA-FT was trained on.

On a CPU the NA-FT models are trained before the crawl ends, on a frozen snapshot of the
collection (its training split only). When the full collection is built, clusters can merge
and a cluster's most common title, which decides its split, can change, so a song that was a
training song in the snapshot may now belong to a dev or test cluster. Queries about such
clusters are removed from every query file, so that all systems are compared on the same
queries and no fine-tuned model has seen a target. Run it after the queries are built and
the LLM queries are synced, and before any retrieval run.

    python scripts/check_naft_overlap.py --snapshot ~/work_train --work work_full --drop
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from visong.corpus import read_jsonl, write_jsonl   # noqa: E402

QUERY_FILES = ("rule.jsonl", "noise_sweep.jsonl", "llm.jsonl", "llm_extra.jsonl", "human.jsonl")


def overlap(snapshot_songs, songs):
    """Clusters of `songs` that are dev/test now and hold a snapshot training song."""
    seen = {s["doc_id"] for s in snapshot_songs if s["extra"].get("split") == "train"}
    now = {s["doc_id"]: s for s in songs}
    moved = sorted(d for d in seen if d in now and now[d]["extra"]["split"] != "train")
    return seen, moved, {now[d]["extra"]["cluster"] for d in moved}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True, help="work folder the NA-FT models were trained on")
    ap.add_argument("--work", required=True)
    ap.add_argument("--drop", action="store_true", help="rewrite the query files without those queries")
    a = ap.parse_args(argv)
    songs = read_jsonl(os.path.join(a.work, "corpus.jsonl"))
    seen, moved, bad = overlap(read_jsonl(os.path.join(a.snapshot, "corpus.jsonl")), songs)
    cluster_of = {s["doc_id"]: s["extra"]["cluster"] for s in songs}
    split_of = {s["doc_id"]: s["extra"]["split"] for s in songs}
    out = {"snapshot_train_songs": len(seen), "snapshot_train_songs_in_collection": sum(d in split_of for d in seen),
           "now_dev_or_test": len(moved), "by_split": dict(Counter(split_of[d] for d in moved)),
           "clusters": len(bad), "queries_dropped": {}, "dropped": a.drop}
    for name in QUERY_FILES:
        path = os.path.join(a.work, "queries", name)
        if not os.path.exists(path):
            continue
        qs = read_jsonl(path)
        keep = [q for q in qs if q["split"] == "train" or cluster_of.get(q["doc_id"]) not in bad]
        out["queries_dropped"][name] = len(qs) - len(keep)
        if a.drop and len(keep) < len(qs):
            write_jsonl(path, keep)
    os.makedirs(os.path.join(a.work, "results"), exist_ok=True)
    json.dump(out, open(os.path.join(a.work, "results", "naft_overlap.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
