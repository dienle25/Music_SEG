"""Sample failed test queries of one run for error analysis.

    python3 -I scripts/sample_failures.py work_full <run name> [--per-type 8] [--types Q3,Q4,Q5] [--seed 1]

For each sampled query whose target cluster is not in the top 10, prints the type, the query,
the rank of the target (inf = not in the top 100), the target's title and artist, and the
titles of the first three retrieved clusters. Also prints the failure rate per type.
"""
import argparse
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong import pipeline as P                      # noqa: E402
from visong.evaluate import per_query, read_trec      # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("run")
    ap.add_argument("--per-type", type=int, default=8)
    ap.add_argument("--types", default="Q3,Q4,Q5")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    songs, cluster_of = P.load(a.work)
    title = {s.doc_id: f"{s.title} / {s.artist}" for s in songs}
    members = defaultdict(list)
    for d, c in cluster_of.items():
        members[c].append(d)
    queries = [q for q in P.load_queries(a.work) if q["split"] == "test"]
    run = read_trec(os.path.join(a.work, "runs", a.run + ".trec"))
    pq = per_query(run, queries, cluster_of)
    rng = random.Random(a.seed)
    for t in a.types.split(","):
        qs = [q for q in queries if q["type"] == t and q["qid"] in pq]
        failed = [q for q in qs if pq[q["qid"]]["rank"] > 10]
        print(f"== {t}: {len(failed)}/{len(qs)} queries miss the top 10")
        for q in rng.sample(failed, min(a.per_type, len(failed))):
            top, seen = [], set()
            for d, _ in run.get(q["qid"], []):
                c = cluster_of.get(d, d)
                if c not in seen:
                    seen.add(c)
                    top.append(title.get(d, d))
                if len(top) == 3:
                    break
            target = " || ".join(title.get(d, d) for d in sorted(members.get(q["cluster"], []))[:2])
            print(f"- [{pq[q['qid']]['rank']}] {q['text']}\n    target: {target}\n    got: {' | '.join(top)}")


if __name__ == "__main__":
    main()
