"""Test-only query variants that check whether the findings depend on how queries were made.

    python3 -I scripts/build_variants.py work_full

Q3u  each test Q3 query typed without marks (15% keep 1-2 Telex syllables, as Q2 does):
     noisy and unmarked at once.
Q4s  a second paraphrase prompt: 5-12 syllables, the length of Q1, from the same fragment as Q4
     (llm_prep/variants/q4s_out.jsonl).
Q5g  a second description prompt that never shows the lyrics or the singer: the writer sees only
     a one-line gist of the song's content (gist_out.jsonl -> q5g_out.jsonl).
Q4s and Q5g go through the filters of the LLM queries (visong.queries.check_llm_query); a query
that fails is left out and counted. Writes WORK/queries/variants.jsonl and
WORK/results/variants_stats.json.
"""
import json
import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong import corpus as C                                   # noqa: E402
from visong.noise import add_telex_residue                       # noqa: E402
from visong.queries import check_llm_query                       # noqa: E402
from visong.text import clean, remove_diacritics                 # noqa: E402


def read_out(path):
    if not os.path.exists(path):
        return {}
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    return {r["id"]: r for r in rows}


def main(work, seed=17, telex_prob=0.15):
    songs = {s.doc_id: s for s in C.load_corpus(os.path.join(work, "corpus.jsonl"))}
    rule = C.read_jsonl(os.path.join(work, "queries", "rule.jsonl"))
    llm = C.read_jsonl(os.path.join(work, "queries", "llm.jsonl"))
    rng, out, stats = random.Random(seed), [], {}

    # Q3u
    n_telex = 0
    for q in rule:
        if q["type"] != "Q3" or q["split"] != "test":
            continue
        plain = remove_diacritics(q["text"])
        use = rng.random() < telex_prob
        text = add_telex_residue(q["text"], plain, rng) if use else plain
        n_telex += use and text != plain
        out.append({**q, "qid": q["qid"].replace("-Q3-", "-Q3u-"), "type": "Q3u", "text": text,
                    "meta": {**q["meta"], "from": q["qid"], "telex": bool(use)}})
    stats["Q3u"] = {"queries": sum(q["type"] == "Q3u" for q in out), "telex": n_telex}

    # Q4s and Q5g, one per test cluster that has a Q4 / Q5
    d = os.path.join(work, "llm_prep", "variants")
    # Items are keyed by the song the LLM query is about or, for a few, by its cluster.
    src, src5 = {}, {}
    for q in llm:
        if q["split"] == "test" and q["type"] in ("Q4", "Q5"):
            for key in (q["cluster"], q["doc_id"]):
                (src if q["type"] == "Q4" else src5)[key] = q
    for qtype, path, base, check_as in (("Q4s", "q4s_out.jsonl", src, "Q4"), ("Q5g", "q5g_out.jsonl", src5, "Q5")):
        rows = read_out(os.path.join(d, path))
        if not rows:
            continue
        why, lengths = Counter(), []
        for key, r in rows.items():
            b = base.get(key)
            if b is None:
                why["no_source_query"] += 1
                continue
            doc_id = b["doc_id"]
            text = clean(r["text"]) if qtype == "Q4s" else r["text"].strip().lower()
            frag = b["meta"].get("source_fragment") if qtype == "Q4s" else None
            ok, reason = check_llm_query(check_as, text, songs[doc_id], frag)
            n = len(clean(text).split())
            if ok and qtype == "Q4s" and not 4 <= n <= 14:
                ok, reason = False, "length"
            if not ok:
                why[reason.split("_")[0] if reason.startswith("copied") else reason] += 1
                continue
            lengths.append(n)
            out.append({"qid": f"{doc_id}-{qtype}-0", "type": qtype, "text": text, "doc_id": doc_id,
                        "cluster": b["cluster"], "split": "test",
                        "meta": {"generator": "llm", "prompt": qtype, "from": b["qid"], "length": n,
                                 **({"source_fragment": frag} if frag else {})}})
        stats[qtype] = {"written": len(rows), "kept": len(lengths), "rejected": dict(why),
                        "mean_length": round(sum(lengths) / max(1, len(lengths)), 2)}
    C.write_jsonl(os.path.join(work, "queries", "variants.jsonl"), out)
    json.dump(stats, open(os.path.join(work, "results", "variants_stats.json"), "w"), indent=1)
    print(json.dumps(stats))


if __name__ == "__main__":
    main(sys.argv[1])
