"""Re-attach LLM/human queries to the current corpus after a rebuild.

Cluster ids and lyrics can change when the cleaning rules change, so this
script copies the current cluster and split of each query's song and runs the
leak/copy filter again on the cleaned lyrics. Q4/Q5 written for the full collection
whose cluster is outside the hash sample (`queries.LLM_SAMPLE`) are moved to
llm_extra.jsonl, and back again if the sample grows; pilot queries (meta.batch
"pilot") always stay.

    python3 scripts/sync_llm_queries.py work_pilot [llm.jsonl]
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from visong.corpus import load_corpus, read_jsonl, write_jsonl   # noqa: E402
from visong.queries import (LLM_SAMPLE, check_llm_query, in_llm_sample, representatives,  # noqa: E402
                            write_unmarked_variants)


def main(work, name="llm.jsonl"):
    songs = {s.doc_id: s for s in load_corpus(os.path.join(work, "corpus.jsonl"))}
    path = os.path.join(work, "queries", name)
    queries = read_jsonl(path)
    # the hash sample applies to collections built with cluster keys (not to the pilot bundle)
    extra_path = os.path.join(work, "queries", "llm_extra.jsonl")
    sampled = name == "llm.jsonl" and any("cluster_key" in s.extra for s in songs.values())
    if sampled and os.path.exists(extra_path):
        queries += read_jsonl(extra_path)
    kept, extra, dropped, changed = [], [], collections.Counter(), 0
    for q in queries:
        s = songs.get(q["doc_id"])
        if s is None:
            dropped["song_removed"] += 1
            continue
        cluster, split = s.extra["cluster"], s.extra["split"]
        if (cluster, split) != (q["cluster"], q["split"]):
            changed += 1
        q["cluster"], q["split"] = cluster, split
        if q["type"] in ("Q4", "Q5"):
            ok, why = check_llm_query(q["type"], q["text"], s, q.get("meta", {}).get("source_fragment"))
            if not ok:
                dropped[why] += 1
                print("DROP", q["qid"], why, q["text"])
                continue
        if sampled and q.get("meta", {}).get("batch") != "pilot" and not in_llm_sample(s):
            extra.append(q)
            continue
        kept.append(q)
    write_jsonl(path, kept)
    if sampled:
        write_jsonl(extra_path, extra)
    if name == "llm.jsonl":
        write_unmarked_variants(work)
    cover = collections.defaultdict(set)
    for q in kept:
        cover[q["split"]].add(q["cluster"])
    clusters = collections.defaultdict(set)
    for s in songs.values():
        clusters[s.extra["split"]].add(s.extra["cluster"])
    print(f"{len(kept)} kept, {len(extra)} outside the sample, {changed} re-labelled, dropped {dict(dropped)}")
    # pilot clusters with queries + sampled clusters that can be a target (not only medleys)
    target = collections.defaultdict(set)
    if sampled:
        reps = representatives(list(songs.values()), {d: s.extra["cluster"] for d, s in songs.items()})
        for c, d in reps.items():
            if in_llm_sample(songs[d]):
                target[songs[d].extra["split"]].add(c)
    for sp in ("dev", "test"):
        line = f"  {sp}: queries cover {len(cover[sp])}/{len(clusters[sp])} clusters"
        if sampled:
            want = target[sp] | cover[sp]
            line += f"; sample ({LLM_SAMPLE[sp]:.0%} + pilot) {len(cover[sp] & want)}/{len(want)}"
        print(line)


if __name__ == "__main__":
    main(*sys.argv[1:])
