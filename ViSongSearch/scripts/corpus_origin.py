"""Where the songs of the final catalogue come from: the uniform crawl or the pilot's focused crawl.

    python3 -I scripts/corpus_origin.py <crawl .jsonl> work_full work_pilot

Writes WORK/results/corpus_origin.json: songs of the final catalogue that the uniform crawl of
Hop Am Chuan fetched, songs that only the focused crawl of the pilot catalogue holds (by site), and
pilot songs that both crawls fetched (the uniform crawl's copy is kept).
"""
import json
import os
import sys
from collections import Counter


def ids(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main(argv=None):
    crawl_path, work, pilot_work = (argv or sys.argv[1:])[:3]
    crawled = {r["doc_id"] for r in ids(crawl_path)}
    final = {r["doc_id"]: r.get("source", "") for r in ids(os.path.join(work, "corpus.jsonl"))}
    pilot = {r["doc_id"] for r in ids(os.path.join(pilot_work, "corpus.jsonl"))}
    uniform = [d for d in final if d in crawled]
    only_pilot = [d for d in final if d not in crawled]
    out = {"final_songs": len(final), "uniform_crawl": len(uniform),
           "pilot_songs": len(pilot), "pilot_in_final": len(pilot & set(final)),
           "pilot_also_crawled": len(pilot & crawled),
           "only_pilot": len(only_pilot), "only_pilot_by_source": dict(Counter(final[d] for d in only_pilot)),
           "not_in_either": len([d for d in final if d not in crawled and d not in pilot])}
    path = os.path.join(work, "results", "corpus_origin.json")
    json.dump(out, open(path, "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
