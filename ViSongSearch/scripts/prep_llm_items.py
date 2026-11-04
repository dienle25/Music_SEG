"""Items for the LLM queries: one representative song per dev/test cluster and a lyric fragment for Q4.

    python scripts/prep_llm_items.py work [--test 600 --dev 150]

The representative is the version the rule queries ask about (`queries.representatives`). With
--sample, only clusters in the hash sample (`queries.LLM_SAMPLE`) are kept, lowest hash first, so
stopping part way still leaves a uniform sample. With --test/--dev, a random sample of that many
clusters per split is kept. Items go to <work>/llm_prep/items.json.
"""
import argparse
import collections
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong.corpus import composer_of, load_corpus, singers_of, vietnamese_ratio  # noqa: E402
from visong.queries import in_llm_sample, representatives, sample_key   # noqa: E402
from visong.text import clean                                      # noqa: E402


def good_fragment(text: str) -> bool:
    """Two lines worth paraphrasing: at least 10 syllables, mostly distinct ("ha há ha" is not),
    mostly Vietnamese."""
    syl = clean(text).split()
    return len(syl) >= 10 and len(set(syl)) >= 0.6 * len(syl) and vietnamese_ratio(text) >= 0.3


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work")
    ap.add_argument("--test", type=int, help="sample this many test clusters")
    ap.add_argument("--dev", type=int, help="sample this many dev clusters")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--skip-covered", action="store_true",
                    help="leave out clusters that already have queries in queries/llm.jsonl")
    ap.add_argument("--out", default="items.json", help="file name in <work>/llm_prep/")
    ap.add_argument("--sample", action="store_true", help="only clusters in the hash sample, lowest hash first")
    a = ap.parse_args(argv)
    songs = load_corpus(os.path.join(a.work, "corpus.jsonl"))
    by_id = {s.doc_id: s for s in songs}
    cluster_of = {s.doc_id: s.extra["cluster"] for s in songs}
    reps = representatives(songs, cluster_of)
    covered = set()
    llm = os.path.join(a.work, "queries", "llm.jsonl")
    if a.skip_covered and os.path.exists(llm):
        covered = {json.loads(l)["cluster"] for l in open(llm, encoding="utf-8") if l.strip()}
    items = []
    for c, d in sorted(reps.items()):
        rep = by_id[d]
        if rep.extra["split"] not in ("test", "dev") or c in covered or (a.sample and not in_llm_sample(rep)):
            continue
        lines = rep.lines()
        cands = [i for i in range(len(lines) - 1) if good_fragment(lines[i] + " " + lines[i + 1])]
        # seeded per song, so a song gets the same fragment whatever else is in the item file
        i = random.Random(f"{a.seed}:{rep.doc_id}").choice(cands) if cands else 0
        items.append({"doc_id": rep.doc_id, "cluster": c, "split": rep.extra["split"], "title": rep.title,
                      "artist": rep.artist, "composer": composer_of(rep), "singers": ", ".join(singers_of(rep)[:3]),
                      "genre": rep.genre, "frag": " / ".join(lines[i:i + 2]), "lyrics_head": " / ".join(lines[:16]),
                      "key": round(sample_key(rep.extra.get("cluster_key", "")), 4)})
    if a.sample:
        items.sort(key=lambda x: x["key"])
    for split, n in (("test", a.test), ("dev", a.dev)):
        if n:
            pool = [x for x in items if x["split"] == split]
            keep = {x["cluster"] for x in random.Random(a.seed).sample(pool, min(n, len(pool)))}
            items = [x for x in items if x["split"] != split or x["cluster"] in keep]
    os.makedirs(os.path.join(a.work, "llm_prep"), exist_ok=True)
    json.dump(items, open(os.path.join(a.work, "llm_prep", a.out), "w"), ensure_ascii=False, indent=0)
    print(len(items), collections.Counter(x["split"] for x in items))


if __name__ == "__main__":
    main()
