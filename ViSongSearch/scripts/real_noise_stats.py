"""Check the query noise model against text typed by real users (no participants needed).

    python3 -I scripts/real_noise_stats.py <raw posts .jsonl> <work>

A. Diacritic use. For every raw post, the marks it carries are compared with what fully
   marked Vietnamese would carry for the same unmarked syllables (estimated from clearly
   marked posts). Posts in other languages (few syllables that are Vietnamese) are left out;
   the rest are marked (omission < 0.1), partly marked or unmarked (omission > 0.9).
B. Variation between independent transcriptions of one song. Versions in the same cluster
   (posted by different users or sites) are aligned syllable by syllable; each substituted
   syllable is classed like the operations of visong.noise: initial, final or tone confusion,
   a one-letter (typing) difference, another vowel mark, or a different word. The shares of the
   first four estimate the channel weights that visong.noise.DEFAULT_WEIGHTS sets by hand.
Writes WORK/results/real_noise.json.
"""
import difflib
import itertools
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong.noise import DEFAULT_WEIGHTS, FINAL_CONFUSIONS, INITIAL_CONFUSIONS, split_initial  # noqa: E402
from visong.text import has_diacritics, remove_diacritics, remove_tones, syllables, tone_of  # noqa: E402

MIN_SYL, MIN_VIET, MAX_PAIRS = 30, 0.85, 3


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def diacritic_use(posts):
    toks = [syllables(p.get("lyrics") or "") for p in posts]
    marked_posts = [t for t in toks if len(t) >= MIN_SYL and np.mean([has_diacritics(s) for s in t]) >= 0.5]
    seen, marked = Counter(), Counter()
    for t in marked_posts:
        for s in t:
            k = remove_diacritics(s)
            seen[k] += 1
            marked[k] += has_diacritics(s)
    p_mark = {k: marked[k] / n for k, n in seen.items() if n >= 3}
    rates, classes = [], Counter()
    for t in toks:
        if len(t) < MIN_SYL:
            classes["short"] += 1
            continue
        # Vietnamese syllables are those seen with marks in marked posts; English words that
        # occur in mixed lyrics ('you', 'look') never carry marks and so do not count.
        viet = [s for s in t if p_mark.get(remove_diacritics(s), 0) > 0]
        strong = sum(p_mark[remove_diacritics(s)] >= 0.5 for s in viet)
        if len(viet) / len(t) < MIN_VIET or strong < 0.3 * len(viet):
            classes["other_language"] += 1
            continue
        expected = sum(p_mark[remove_diacritics(s)] for s in viet)
        observed = sum(has_diacritics(s) for s in viet)
        om = float(min(1.0, max(0.0, 1 - observed / expected))) if expected else 0.0
        rates.append(om)
        classes["unmarked" if om > 0.9 else "partly_marked" if om >= 0.1 else "marked"] += 1
    part = [r for r in rates if 0.1 <= r <= 0.9]
    viet_n = len(rates)
    return {"posts": len(posts), "classes": dict(classes), "vietnamese_posts": viet_n,
            "share_unmarked": round(classes["unmarked"] / viet_n, 4) if viet_n else None,
            "share_partly": round(classes["partly_marked"] / viet_n, 4) if viet_n else None,
            "partly_omission_quartiles": [round(float(q), 3) for q in np.percentile(part, [25, 50, 75])] if part else None,
            "mean_omission": round(float(np.mean(rates)), 4) if rates else None}, p_mark


def edit1(a: str, b: str) -> bool:
    """True when two strings differ by one inserted, deleted, replaced or swapped letter."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]]
                                  and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def classify(x: str, y: str) -> str:
    """Kind of one substituted syllable; 'orthographic' marks spelling conventions, not errors
    (tone placed on another vowel as in hoá/hóa, or i for y as in ký/kí)."""
    if tone_of(x) == tone_of(y) and remove_tones(x).replace("y", "i") == remove_tones(y).replace("y", "i"):
        return "orthographic"
    if remove_tones(x) == remove_tones(y):
        return "tone"
    if remove_diacritics(x) == remove_diacritics(y):
        return "vowel_mark"
    (ix, rx), (iy, ry) = split_initial(remove_tones(x)), split_initial(remove_tones(y))
    if rx == ry and ix != iy:
        return "initial_known" if iy in INITIAL_CONFUSIONS.get(ix, []) else "initial_other"
    if ix == iy:
        for f1, f2 in FINAL_CONFUSIONS:
            if rx.endswith(f1) and ry.endswith(f2) and rx[:-len(f1)] == ry[:-len(f2)]:
                return "final"
    if edit1(remove_diacritics(x), remove_diacritics(y)):
        return "one_letter"
    return "other_word"


def transcription_variation(work, p_mark):
    """p_mark: unmarked syllable -> share marked in Vietnamese posts; a syllable never seen with
    marks (English 'are', 'you') is not Vietnamese and its substitutions are left out."""
    viet = lambda s: p_mark.get(remove_diacritics(s), 0) > 0                     # noqa: E731
    songs = {r["doc_id"]: r for r in read_jsonl(os.path.join(work, "corpus.jsonl"))}
    cluster_of = json.load(open(os.path.join(work, "clusters.json")))
    groups = defaultdict(list)
    for d, c in cluster_of.items():
        if d in songs:
            groups[c].append(d)
    kinds, aligned, pairs, indel = Counter(), 0, 0, 0
    per_pair, sub_pairs, cross = [], [], 0
    for c, docs in groups.items():
        if len(docs) < 2:
            continue
        for a, b in list(itertools.combinations(sorted(docs), 2))[:MAX_PAIRS]:
            sa, sb = syllables(songs[a]["lyrics"]), syllables(songs[b]["lyrics"])
            m = difflib.SequenceMatcher(None, sa, sb, autojunk=False)
            if m.ratio() < 0.5:                       # medleys or different songs with one title
                continue
            pairs += 1
            is_cross = source_of(a) != source_of(b)
            cross += is_cross
            pk, pa = Counter(), 0
            for op, i1, i2, j1, j2 in m.get_opcodes():
                if op == "equal":
                    pa += i2 - i1
                elif op == "replace" and i2 - i1 == j2 - j1 and i2 - i1 <= 3:
                    pa += i2 - i1
                    for x, y in zip(sa[i1:i2], sb[j1:j2]):
                        k = classify(x, y) if viet(x) and viet(y) else "not_vietnamese"
                        pk[k] += 1
                        if k not in ("orthographic", "not_vietnamese"):
                            sub_pairs.append({"a": x, "b": y, "kind": k, "cross_source": is_cross})
                else:
                    indel += max(i2 - i1, j2 - j1)
            kinds.update(pk)
            aligned += pa
            per_pair.append((pk, pa, is_cross))
    subs = sum(v for k, v in kinds.items() if k not in ("orthographic", "not_vietnamese"))
    channel = {"initial": kinds["initial_known"] + kinds["initial_other"], "final": kinds["final"],
               "tone": kinds["tone"], "typo": kinds["one_letter"] + kinds["vowel_mark"]}
    tot = sum(channel.values())
    return {"pairs": pairs, "cross_source_pairs": cross, "aligned_syllables": aligned, "substitutions": subs,
            "indel_syllables": indel,
            "per_1000_aligned": round(1000 * subs / aligned, 2) if aligned else None,
            "kinds": dict(kinds), "channel_counts": channel,
            "channel_shares": {k: round(v / tot, 3) for k, v in channel.items()} if tot else None,
            "default_weights": DEFAULT_WEIGHTS,
            "known_initial_share": round(kinds["initial_known"] / max(1, channel["initial"]), 3),
            "kind_shares_ci95": bootstrap_shares(per_pair),
            "cross_source": summarize_pairs([p for p in per_pair if p[2]]),
            "same_source": summarize_pairs([p for p in per_pair if not p[2]]),
            "substitution_pairs": sub_pairs}


SUB_KINDS = ("other_word", "tone", "initial_known", "initial_other", "final", "vowel_mark", "one_letter")


def source_of(doc_id: str) -> str:
    """Site a version comes from, read off its id prefix: hac<number> (Hợp Âm Chuẩn),
    nhaccu<hex> and nhac<hex> (the two databases of the course repository)."""
    for prefix in ("hac", "nhaccu", "nhac"):
        if doc_id.startswith(prefix):
            return prefix
    return "other"


def summarize_pairs(per_pair):
    kinds, aligned = Counter(), 0
    for pk, pa, _ in per_pair:
        kinds.update(pk)
        aligned += pa
    subs = sum(kinds[k] for k in SUB_KINDS)
    return {"pairs": len(per_pair), "aligned_syllables": aligned, "substitutions": subs,
            "per_1000_aligned": round(1000 * subs / aligned, 2) if aligned else None,
            "kind_shares": {k: round(kinds[k] / subs, 3) for k in SUB_KINDS} if subs else None}


def bootstrap_shares(per_pair, n_boot: int = 2000, seed: int = 0):
    """95% percentile intervals of each kind's share among substitutions, resampling version
    pairs (a pair's substitutions are not independent of each other)."""
    rng = np.random.default_rng(seed)
    counts = np.array([[pk[k] for k in SUB_KINDS] for pk, _, _ in per_pair], dtype=float)
    aligned = np.array([pa for _, pa, _ in per_pair], dtype=float)
    if not len(counts):
        return None
    shares, rates = [], []
    for _ in range(n_boot):
        idx = rng.integers(0, len(counts), len(counts))
        tot = counts[idx].sum(axis=0)
        if tot.sum():
            shares.append(tot / tot.sum())
            rates.append(1000 * tot.sum() / max(1.0, aligned[idx].sum()))
    shares = np.array(shares)
    out = {k: [round(float(np.percentile(shares[:, i], 2.5)), 3), round(float(np.percentile(shares[:, i], 97.5)), 3)]
           for i, k in enumerate(SUB_KINDS)}
    out["per_1000_aligned"] = [round(float(np.percentile(rates, 2.5)), 2), round(float(np.percentile(rates, 97.5)), 2)]
    return out


def main(argv=None):
    argv = argv or sys.argv[1:]
    raw, work = argv[0], argv[1]
    dia, p_mark = diacritic_use(read_jsonl(raw))
    out = {"diacritics": dia, "transcriptions": transcription_variation(work, p_mark)}
    os.makedirs(os.path.join(work, "results"), exist_ok=True)
    json.dump(out, open(os.path.join(work, "results", "real_noise.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
