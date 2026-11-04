"""Descriptive statistics of the test collection (corpus, clusters, queries).

    python3 scripts/collection_stats.py work_pilot

Writes results/collection_stats.json with, per query type and split: counts,
length in syllables, share of query syllables (diacritic-free) found in the target
song's lyrics, longest run copied from the lyrics, share typed without marks, share
that names an artist of the song, the most common first word, and the noise
operations applied to Q3 queries.
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong.queries import QUERY_FILES                     # noqa: E402
from visong.corpus import load_corpus, read_jsonl            # noqa: E402
from visong.queries import longest_common_run                # noqa: E402
from visong.text import char_ngrams, clean, has_diacritics, remove_diacritics, syllables   # noqa: E402


def describe(values):
    v = np.asarray(values, float)
    if not len(v):
        return {}
    return {"mean": round(float(v.mean()), 3), "median": round(float(np.median(v)), 3),
            "p10": round(float(np.quantile(v, 0.1)), 3), "p90": round(float(np.quantile(v, 0.9)), 3)}


def artist_names(artist: str) -> list[list[str]]:
    """Unmarked syllables of each name in an artist field ("A, B ft. C" -> three names)."""
    parts = re.split(r",|&|;|/|\bft\.?(?=\s)|\bfeat\.?(?=\s)|\bx\b", (artist or "").lower())
    return [toks for p in parts if (toks := remove_diacritics(clean(p)).split())]


def names_artist(tokens: list[str], names: list[list[str]]) -> bool:
    return any(tokens[i:i + len(n)] == n for n in names for i in range(len(tokens) - len(n) + 1))


def main(work="work_pilot"):
    songs = load_corpus(os.path.join(work, "corpus.jsonl"))
    artists_of = {s.doc_id: artist_names(s.artist) for s in songs}
    by_cluster = defaultdict(list)
    for s in songs:
        by_cluster[s.extra["cluster"]].append(s)
    lyric_tokens = {c: remove_diacritics(clean(" ".join(m.lyrics for m in ms))).split() for c, ms in by_cluster.items()}
    lyric_sets = {c: set(t) for c, t in lyric_tokens.items()}
    stats = {"songs": len(songs), "clusters": len(by_cluster),
             "songs_per_cluster": dict(Counter(len(v) for v in by_cluster.values())),
             "syllables_per_song": describe([len(syllables(s.lyrics)) for s in songs]),
             "lines_per_song": describe([len(s.lines()) for s in songs]),
             "sources": dict(Counter(s.source for s in songs)),
             "split_songs": dict(Counter(s.extra["split"] for s in songs)),
             "split_clusters": dict(Counter(ms[0].extra["split"] for ms in by_cluster.values())),
             "with_artist": sum(bool(s.artist) for s in songs), "with_genre": sum(bool(s.genre) for s in songs)}
    # how ambiguous unmarked syllables are, and how unselective character trigrams are
    tokens = [t for s in songs for t in remove_diacritics(clean(s.lyrics)).split()]
    marked = Counter(t for s in songs for t in clean(s.lyrics).split())
    forms = defaultdict(set)
    for t in marked:
        forms[remove_diacritics(t)].add(t)
    lens = np.array([len(t) for t in tokens])
    types = Counter(tokens)
    by_gram = defaultdict(set)
    for t in types:
        for g in char_ngrams(t, 3):
            by_gram[g].add(t)
    share = np.array([np.mean([len(by_gram[g]) for g in char_ngrams(t, 3)]) for t in types])
    weight = np.array([types[t] for t in types], float)
    stats["syllables"] = {
        "marked_types": len(marked), "unmarked_types": len(types),
        "letters_median": float(np.median(lens)), "share_2_to_5_letters": round(float(np.mean((lens >= 2) & (lens <= 5))), 3),
        "marked_forms_per_unmarked_token": round(float(np.mean([len(forms[t]) for t in tokens])), 2),
        "syllable_types_per_trigram_token": round(float((share * weight).sum() / weight.sum()), 1),
        "forms_of_bo": sorted(forms.get("bo", []))}
    passages = read_jsonl(os.path.join(work, "passages.jsonl"))
    stats["passages"] = len(passages)
    stats["passage_syllables"] = describe([len(p["text"].split()) for p in passages])

    queries = []
    for f in QUERY_FILES:
        path = os.path.join(work, "queries", f)
        if os.path.exists(path):
            queries += read_jsonl(path)
    groups = defaultdict(list)
    for q in queries:
        groups[(q["type"], q["split"])].append(q)
    qstats = {}
    for (t, split), qs in sorted(groups.items()):
        lens, overlap, runs, plain, base, artist, first = [], [], [], [], [], [], Counter()
        rng = np.random.default_rng(0)
        cluster_ids = sorted(lyric_sets)
        for q in qs:
            toks = remove_diacritics(clean(q["text"])).split()
            if not toks:
                continue
            lens.append(len(toks))
            overlap.append(sum(x in lyric_sets.get(q["cluster"], set()) for x in toks) / len(toks))
            other = cluster_ids[int(rng.integers(len(cluster_ids)))]
            while other == q["cluster"]:
                other = cluster_ids[int(rng.integers(len(cluster_ids)))]
            base.append(sum(x in lyric_sets[other] for x in toks) / len(toks))
            runs.append(longest_common_run(toks, lyric_tokens.get(q["cluster"], [])))
            plain.append(not has_diacritics(q["text"]))
            artist.append(names_artist(toks, artists_of.get(q.get("doc_id"), [])))
            first[clean(q["text"]).split()[0]] += 1
        word, count = first.most_common(1)[0] if first else ("", 0)
        row = {"n": len(qs), "length": describe(lens), "lyric_overlap": describe(overlap),
               "overlap_with_random_song": describe(base),
               "longest_copied_run": describe(runs), "share_without_marks": round(float(np.mean(plain)), 3),
               # descriptions written to a template: do they name the artist, start alike?
               "share_naming_artist": round(float(np.mean(artist)), 3) if artist else None,
               "first_word": {"word": word, "share": round(count / max(1, sum(first.values())), 3)}}
        if t == "Q3":
            ops = Counter()
            rates = []
            for q in qs:
                info = q["meta"].get("noise", q["meta"])
                ops.update(info.get("ops", {}))
                if "changed" in info and info.get("length"):
                    rates.append(info["changed"] / info["length"])
            row["noise_ops"] = dict(ops)
            if rates:
                row["changed_share"] = describe(rates)
        qstats[f"{t}/{split}"] = row
    stats["queries"] = qstats
    sweep = read_jsonl(os.path.join(work, "queries", "noise_sweep.jsonl"))
    stats["noise_sweep"] = dict(Counter(q["type"] for q in sweep))
    out = os.path.join(work, "results", "collection_stats.json")
    json.dump(stats, open(out, "w"), indent=1, ensure_ascii=False)
    for k, v in qstats.items():
        print(k, v["n"], "len", v["length"].get("mean"), "overlap", v["lyric_overlap"].get("mean"),
              "random", v["overlap_with_random_song"].get("mean"),
              "run", v["longest_copied_run"].get("mean"), "plain", v["share_without_marks"])
    print("wrote", out)


if __name__ == "__main__":
    main(*sys.argv[1:])
