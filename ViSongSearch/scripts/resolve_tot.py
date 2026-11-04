"""Map the answers of harvested tip-of-the-tongue posts to song clusters of the collection.

    python scripts/resolve_tot.py work_full realq/harvest.jsonl [more.jsonl ...] --out realq/resolved.jsonl

The answer text, the titles of its YouTube/Spotify links (answer_link_titles) and the words in the
paths of music-site links are compared with every song's title and with its composer and singers,
all lowercased and without diacritics (titles through dedup.norm_title, the rule that names the
clusters). Evidence for a song:
- a Hợp Âm Chuẩn link to it (confidence 1);
- its title as a run of syllables of the answer (two syllables or more), or as one segment of it
  (a line, a quoted span, either side of "Title - Artist", "by", "của"), exactly or with a small
  edit (difflib ratio >= FUZZY);
- one of its composers or singers named in the answer.
The confidence is the title evidence weighted by title length (one-syllable titles say little) and
raised by a name match; without one it stays at or below 0.85. Medleys are never targets. The best
cluster is kept when its confidence reaches MATCH; below that the song is taken to be outside the
collection.

needs_manual_check is set, with check_reasons, when the answer was not confirmed by the asker
(asker_thanked, flair_top, reply: weak_answer_method), the confidence is below ACCEPT, no composer
or singer confirms the title (no_name_match), the next cluster is within MARGIN (ambiguous), nothing
matched (no_match) or the post has no answer (no_answer).

Only the answer decides the target. query_lyrics_hits, the number of syllable trigrams of the query
found in the matched song's lyrics, is there to speed up the manual check and never changes the
confidence: choosing real queries by how well they match the lyrics would favour lexical systems.

Writes the resolved records (input fields plus cluster_id, doc_id, matched_title, matched_artist,
split, confidence, match_method, candidates, needs_manual_check, check_reasons, query_lyrics_hits),
a CSV for the manual check (<out>_check.csv, with empty verified_cluster_id and note columns) and
counts (<out>_stats.json). No lyrics are written.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong.corpus import composer_of, credits, is_compilation_song, load_corpus, singers_of  # noqa: E402
from visong.dedup import norm_title                                                          # noqa: E402
from visong.text import clean, has_diacritics, remove_diacritics                             # noqa: E402

ACCEPT = 0.9      # confidence needed to skip the manual check
MATCH = 0.6       # below this the answer is taken to name a song outside the collection
MARGIN = 0.1      # the best cluster must lead the next one by this much
FUZZY = 0.8       # difflib ratio of a title segment with a small edit
NAMED = 0.85      # name score that counts as a composer or singer match
CONFIRMED = {"asker_solved", "asker_confirmed", "asker_self", "tomtkis_heuristic"}

URL = re.compile(r"https?://[^\s<>\"'\])]+", re.I)
MD_LINK = re.compile(r"\[([^\]]{1,200})\]\(https?://[^)\s]+\)")
HAC = re.compile(r"hopamchuan\.com/song/(\d+)", re.I)
SLUG_SITE = re.compile(r"nhaccuatui\.com|zingmp3\.vn|mp3\.zing\.vn|hopamchuan\.com|hopamviet\.vn|nhac\.vn|"
                       r"chiasenhac\.vn|loibaihat|lyric|soundcloud\.com", re.I)
SEP = re.compile(r"\n|[\"“”«»|()\[\]*_~>•·,;!?]|\.(?:\s|$)|\s[-–—:]\s|\s(?:by|của|cua|ca sĩ|ca si|trình bày|"
                 r"trinh bay|sáng tác|sang tac|feat\.?|ft\.?|performed by|sung by)\s", re.I)
FILLER_HEAD = re.compile(r"^(?:(?:yes|yeah|oh|hey|hi|op|bro|bac|ae|ban oi|"
                         r"i think|maybe|probably|perhaps|could be|it s|its|it is|this is|that s|"
                         r"the song is|song is|the song|is it|bai nay la|bai nay ten la|bai do la|bai nay|"
                         r"ten bai la|ten bai|ten la|la bai|bai hat|bai|nghe nhu|chac la|hinh nhu la|"
                         r"co phai la|phai la|la)\s+)+")
FILLER_TAIL = re.compile(r"(?:\s+(?:nhe|nha|ban|ban oi|a|day|do|bro|ban nhe|lol|official|mv|music video|"
                         r"lyrics?|audio|video|hd|4k|official music video|official mv))+$")


def norm(text: str) -> str:
    return remove_diacritics(clean(text or ""))


def trigrams(s: str) -> set[str]:
    s = f" {s} "
    return {s[i:i + 3] for i in range(len(s) - 2)}


def split_names(text: str) -> list[str]:
    return [x for x in re.split(r"\s*(?:,|;|&|/|\+|\bft\.?|\bfeat\.?|\bx\b|\bvà\b|\band\b)\s*", text or "")
            if x.strip()]


class Catalogue:
    """Titles and names of the songs that can be targets (medleys excluded), indexed for matching."""

    def __init__(self, songs, cluster_of: dict):
        self.docs, self.by_title = {}, defaultdict(list)
        self.cluster_docs, self.tri, self._grams = defaultdict(list), defaultdict(set), {}
        for s in songs:
            c, t = cluster_of.get(s.doc_id), norm_title(s.title)
            if c is None or not t or is_compilation_song(s):
                continue
            names = []
            for n in split_names(composer_of(s)) + singers_of(s):
                marked = clean(n)
                if len(marked) >= 2 and marked not in [m for m, _ in names]:
                    names.append((marked, remove_diacritics(marked)))
            self.docs[s.doc_id] = {"cluster": c, "title": s.title, "nsyl": len(t.split()), "names": names,
                                   "artist": credits(s), "split": s.extra.get("split"), "lyrics": s.lyrics}
            self.by_title[t].append(s.doc_id)
            self.cluster_docs[c].append(s.doc_id)
        for t in self.by_title:
            for g in trigrams(t):
                self.tri[g].add(t)
        self.max_syl = min(12, max((len(t.split()) for t in self.by_title), default=1))

    def mentions(self, toks: list[str]) -> list[str]:
        """Titles of two or more syllables found as runs of the tokens; a run inside a longer found
        run is dropped ('chung ta' inside 'chung ta cua hien tai')."""
        spans = []
        for i in range(len(toks)):
            for n in range(min(self.max_syl, len(toks) - i), 1, -1):
                g = " ".join(toks[i:i + n])
                if g in self.by_title:
                    spans.append((i, i + n, g))
                    break
        kept = []
        for a, b, g in sorted(spans, key=lambda s: s[0] - s[1]):
            if not any(x <= a and b <= y for x, y, _ in kept):
                kept.append((a, b, g))
        return [g for _, _, g in kept]

    def fuzzy(self, seg: str, k: int = 40) -> list[tuple[float, str]]:
        """Titles within a small edit of the segment: trigram shortlist, then difflib ratio."""
        grams = trigrams(seg)
        counts = Counter(t for g in grams for t in self.tri.get(g, ()))
        need, out = max(2, len(grams) // 3), []
        for t, n in counts.most_common(k):
            if n < need:
                break
            r = difflib.SequenceMatcher(None, seg, t).ratio()
            if r >= FUZZY:
                out.append((r, t))
        return out

    def lyric_grams(self, doc: str) -> set[str]:
        if doc not in self._grams:
            toks = norm(self.docs[doc]["lyrics"]).split()
            self._grams[doc] = {" ".join(toks[i:i + 3]) for i in range(len(toks) - 2)}
        return self._grams[doc]


def name_score(names, text_marked: str) -> float:
    """How well the text names one of the song's composers or singers (diacritics ignored):
    1 for a name of several words, 0.9 for one written without spaces ('sontung') or with a typo in
    one word of four letters or more, 0.85 for a one-word name ('Đen', 'Erik') found as a whole word,
    with its diacritics when the text has any. Names one letter apart in a short word ('Lê Minh',
    'Lê Linh') are different people, so no other fuzzy match counts."""
    text = remove_diacritics(text_marked)
    toks, best = text.split(), 0.0
    padded, nospace = f" {text} ", text.replace(" ", "")
    for marked, plain in names:
        parts = plain.split()
        if len(parts) == 1:
            marked_ok = has_diacritics(text_marked) and has_diacritics(marked)
            if len(plain) >= 3 and (marked in text_marked.split() if marked_ok else plain in toks):
                best = max(best, 0.85)
            continue
        if f" {plain} " in padded:
            return 1.0
        if len(plain) >= 7 and plain.replace(" ", "") in nospace:
            best = max(best, 0.9)
        for i in range(len(toks) - len(parts) + 1):
            diff = [(a, b) for a, b in zip(parts, toks[i:i + len(parts)]) if a != b]
            if len(diff) == 1 and min(map(len, diff[0])) >= 4 and \
                    difflib.SequenceMatcher(None, *diff[0]).ratio() >= 0.75:
                best = max(best, 0.9)
    return best


def confidence(title_score: float, nsyl: int, named: bool) -> float:
    spec = min(1.0, nsyl / 3)
    if named:
        return round(0.6 + 0.4 * title_score * (0.5 + 0.5 * spec), 3)
    return round(0.85 * title_score * (0.6 + 0.4 * spec), 3)


def slug_words(url: str) -> str:
    """Words of the path of a music-site link: .../bai-hat/chung-ta-cua-hien-tai-son-tung-m-tp.Ab12Cd34.html"""
    parts = []
    for seg in unquote(urlparse(url).path).split("/"):
        seg = re.sub(r"\.(?:html?|php|aspx?)$", "", seg)
        seg = re.sub(r"\.\w{6,}$", "", seg)
        if "-" in seg and re.fullmatch(r"[\w-]+", seg):
            parts.append(seg.replace("-", " "))
    return " ".join(parts)


def evidence_texts(rec: dict) -> tuple[list[str], list[str]]:
    text = rec.get("answer_text") or ""
    links = list(dict.fromkeys(URL.findall(text) + list(rec.get("answer_links") or [])))
    texts = [URL.sub(" ", MD_LINK.sub(r" \1 ", text))] + [t for t in rec.get("answer_link_titles") or [] if t]
    texts += [slug_words(u) for u in links if SLUG_SITE.search(u)]
    return [t for t in texts if t.strip()], links


def segments(text: str) -> list[str]:
    out = []
    for part in SEP.split(text):
        s = FILLER_TAIL.sub("", FILLER_HEAD.sub("", norm_title(part or ""))).strip()
        if s and len(s.split()) <= 12 and s not in out:
            out.append(s)
    return out[:20]


def resolve(rec: dict, cat: Catalogue) -> dict:
    texts, links = evidence_texts(rec)
    found = {}                                     # doc_id -> (title score, evidence)

    def add(doc, score, how):
        if doc in cat.docs and score > found.get(doc, (0.0, ""))[0]:
            found[doc] = (score, how)

    for u in links:
        m = HAC.search(u)
        if m:
            add(f"hac{m.group(1)}", 2.0, "hac_link")
    for t in texts:
        for g in cat.mentions(norm(t).split()[:400]):
            for d in cat.by_title[g]:
                add(d, 1.0, "title_in_text")
        for seg in segments(t):
            if seg in cat.by_title:
                for d in cat.by_title[seg]:
                    add(d, 1.0, "title_segment")
            elif len(seg.split()) >= 2:
                for r, g in cat.fuzzy(seg):
                    for d in cat.by_title[g]:
                        add(d, round(r, 3), "title_fuzzy")
    joined = clean(" ".join(texts))
    clusters = {}
    for d, (score, how) in found.items():
        doc = cat.docs[d]
        named = how == "hac_link" or name_score(doc["names"], joined) >= NAMED
        conf = 1.0 if how == "hac_link" else confidence(score, doc["nsyl"], named)
        c = doc["cluster"]
        if c not in clusters or conf > clusters[c]["confidence"]:
            clusters[c] = {"cluster_id": c, "doc_id": d, "title": doc["title"], "artist": doc["artist"],
                           "split": doc["split"], "confidence": conf, "evidence": how, "name_match": named}
    ranked = sorted(clusters.values(), key=lambda x: (-x["confidence"], x["cluster_id"]))[:5]
    best = ranked[0] if ranked and ranked[0]["confidence"] >= MATCH else None
    reasons = []
    if not (rec.get("answer_text") or "").strip():
        reasons.append("no_answer")
    else:
        if rec.get("answer_method") not in CONFIRMED:
            reasons.append("weak_answer_method")
        if best is None:
            reasons.append("no_match")
    if best is not None:
        if best["confidence"] < ACCEPT:
            reasons.append("low_confidence")
        if not best["name_match"]:
            reasons.append("no_name_match")
        if len(ranked) > 1 and best["confidence"] - ranked[1]["confidence"] < MARGIN:
            reasons.append("ambiguous")
    hits = None
    if best is not None:
        toks = norm(URL.sub(" ", rec.get("query") or "")).split()
        grams = {" ".join(toks[i:i + 3]) for i in range(len(toks) - 2)}
        hits = max(len(grams & cat.lyric_grams(d)) for d in cat.cluster_docs[best["cluster_id"]])
    out = dict(rec)
    out.update({"cluster_id": best["cluster_id"] if best else None, "doc_id": best["doc_id"] if best else None,
                "matched_title": best["title"] if best else None, "matched_artist": best["artist"] if best else None,
                "split": best["split"] if best else None,
                "confidence": ranked[0]["confidence"] if ranked else 0.0,
                "match_method": best["evidence"] if best else None, "candidates": ranked,
                "needs_manual_check": bool(reasons), "check_reasons": reasons, "query_lyrics_hits": hits})
    return out


CSV_COLUMNS = ["id", "url", "needs_manual_check", "check_reasons", "confidence", "cluster_id", "matched_title",
               "matched_artist", "split", "second_candidate", "answer_method", "answer_text", "query",
               "query_lyrics_hits", "verified_cluster_id", "note"]


def _short(s, n):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def write_check_csv(path: str, rows: list[dict]):
    """One row per record for the manual check: open questions first, then the most confident."""
    order = sorted(rows, key=lambda r: (not r["needs_manual_check"], r["answer_text"] is None, -r["confidence"]))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in order:
            second = r["candidates"][1] if len(r["candidates"]) > 1 else None
            w.writerow({"id": r["id"], "url": r.get("url"), "needs_manual_check": int(r["needs_manual_check"]),
                        "check_reasons": " ".join(r["check_reasons"]), "confidence": r["confidence"],
                        "cluster_id": r["cluster_id"] or "", "matched_title": r["matched_title"] or "",
                        "matched_artist": r["matched_artist"] or "", "split": r["split"] or "",
                        "second_candidate": f"{second['title']} ({second['cluster_id']}, {second['confidence']})"
                        if second else "", "answer_method": r.get("answer_method") or "",
                        "answer_text": _short(r.get("answer_text"), 300), "query": _short(r.get("query"), 400),
                        "query_lyrics_hits": "" if r["query_lyrics_hits"] is None else r["query_lyrics_hits"],
                        "verified_cluster_id": "", "note": ""})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("work", help="work folder with corpus.jsonl and clusters.json (e.g. work_full)")
    ap.add_argument("harvest", nargs="+", help="harvest_tot.py output(s); a repeated id keeps its first copy")
    ap.add_argument("--out", required=True, help="resolved .jsonl (_check.csv and _stats.json are written next to it)")
    args = ap.parse_args(argv)
    songs = load_corpus(os.path.join(args.work, "corpus.jsonl"))
    with open(os.path.join(args.work, "clusters.json"), encoding="utf-8") as f:
        cat = Catalogue(songs, json.load(f))
    recs, seen = [], set()
    for path in args.harvest:
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line) if line.strip() else None
                if r and r.get("id") not in seen:
                    seen.add(r.get("id"))
                    recs.append(r)
    out = [resolve(r, cat) for r in recs]
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    stem = args.out[:-6] if args.out.endswith(".jsonl") else args.out
    with open(args.out, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    write_check_csv(stem + "_check.csv", out)
    matched = [r for r in out if r["cluster_id"]]
    auto = [r for r in matched if not r["needs_manual_check"]]
    stats = {"records": len(out), "answered": sum(bool(r.get("answer_text")) for r in out),
             "matched": len(matched), "auto_accepted": len(auto),
             "needs_manual_check": sum(r["needs_manual_check"] for r in out),
             "check_reasons": dict(Counter(x for r in out for x in r["check_reasons"]).most_common()),
             "matched_by_split": dict(Counter(r["split"] for r in matched)),
             "auto_accepted_by_split": dict(Counter(r["split"] for r in auto)),
             "distinct_matched_clusters": len({r["cluster_id"] for r in matched}),
             "by_source": dict(Counter(r.get("source") for r in out)),
             "by_answer_method": dict(Counter(r.get("answer_method") for r in out if r.get("answer_method"))),
             "catalogue": {"songs": len(cat.docs), "titles": len(cat.by_title)},
             "thresholds": {"accept": ACCEPT, "match": MATCH, "margin": MARGIN, "fuzzy": FUZZY}}
    with open(stem + "_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    print(json.dumps(stats, ensure_ascii=False))
    return stats


if __name__ == "__main__":
    main()
