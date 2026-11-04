"""Group copies of the same song into clusters (SEG 3.7 near-duplicate detection).

The same song appears on several sites and in several chord versions. For
known-item search any copy is a correct answer, so evaluation works on clusters.

Two songs are merged when
- their lyrics are near-duplicates: Jaccard of syllable 5-gram shingles
  (diacritics removed) >= `threshold`, or
- their normalised titles are equal and the lyric Jaccard >= `title_threshold`.

MinHash LSH only proposes candidate pairs; every candidate is then checked with
the exact Jaccard of the two shingle sets. With 42 bands of 3 rows a pair at
Jaccard 0.5 becomes a candidate with probability 0.996. The pilot collection
(tag bundle-v7) used datasketch's choice for a 0.5 threshold, 25 bands of 5 rows,
and the MinHash estimate: 0.55 at Jaccard 0.5.

Medleys and mashups are found by content as well as by title: a song that
contains a large part of two or more other clusters (`borrowed_clusters`).
"""
from __future__ import annotations

import re
from collections import defaultdict

import numpy as np
from datasketch import MinHash, MinHashLSH
from scipy import sparse

from .text import clean, remove_diacritics

_TITLE_NOISE = re.compile(r"\(.*?\)|\[.*?\]|\b(remix|live|cover|acoustic|lofi|lo fi|beat|karaoke|"
                          r"ver(sion)?\s*\d*|hợp âm (cơ bản|nâng cao))\b", re.I)


def norm_title(title: str) -> str:
    return remove_diacritics(clean(_TITLE_NOISE.sub(" ", title or "")))


def shingles(text: str, k: int = 5) -> set[str]:
    toks = remove_diacritics(clean(text)).split()
    if len(toks) <= k:
        return {" ".join(toks)}
    return {" ".join(toks[i:i + k]) for i in range(len(toks) - k + 1)}


def minhash(text: str, num_perm: int = 128, k: int = 5) -> MinHash:
    m = MinHash(num_perm=num_perm, seed=1)
    for s in shingles(text, k):
        m.update(s.encode("utf-8"))
    return m


class UnionFind:
    def __init__(self, items):
        self.p = {x: x for x in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b) if len(a) <= len(b) else len(b & a)
    return inter / (len(a) + len(b) - inter)


def candidate_probability(j: float, bands: int, rows: int) -> float:
    """Chance that LSH proposes a pair whose Jaccard is j."""
    return 1.0 - (1.0 - j ** rows) ** bands


def cluster_songs(songs, threshold: float = 0.5, title_threshold: float = 0.05,
                  num_perm: int = 128, rows: int | None = None) -> tuple[dict[str, str], dict]:
    """Return doc_id -> cluster_id (the smallest doc_id in the cluster) and stats.

    Two songs join when their lyric shingles have Jaccard >= threshold, or when they
    share a normalised title and Jaccard >= title_threshold. On the pilot, songs that
    only share a title ("Em", "Yêu") have Jaccard 0.0, while versions of one song with
    badly transcribed remix lyrics still reach 0.09, hence 0.05. LSH uses rows of 3
    (2 below a 0.45 threshold), so it misses few pairs above the threshold."""
    rows = rows or (3 if threshold >= 0.45 else 2)
    bands = num_perm // rows
    sh = {s.doc_id: shingles(s.lyrics) for s in songs}
    mh = {}
    for d, S in sh.items():
        m = MinHash(num_perm=num_perm, seed=1)
        m.update_batch([x.encode("utf-8") for x in S])
        mh[d] = m
    uf = UnionFind(mh)
    lsh = MinHashLSH(num_perm=num_perm, params=(bands, rows))
    candidates = lyric_pairs = 0
    for s in songs:
        for other in lsh.query(mh[s.doc_id]):
            candidates += 1
            if jaccard(sh[s.doc_id], sh[other]) >= threshold:
                uf.union(s.doc_id, other)
                lyric_pairs += 1
        lsh.insert(s.doc_id, mh[s.doc_id])
    by_title = defaultdict(list)
    for s in songs:
        t = norm_title(s.title)
        if t:
            by_title[t].append(s.doc_id)
    title_pairs = 0
    for ids in by_title.values():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if jaccard(sh[ids[i]], sh[ids[j]]) >= title_threshold:
                    uf.union(ids[i], ids[j])
                    title_pairs += 1
    cluster = {d: uf.find(d) for d in mh}
    sizes = defaultdict(int)
    for c in cluster.values():
        sizes[c] += 1
    stats = {"songs": len(songs), "clusters": len(sizes), "lyric_pairs": lyric_pairs,
             "title_pairs": title_pairs, "largest_cluster": max(sizes.values()) if sizes else 0,
             "songs_in_multi_clusters": sum(v for v in sizes.values() if v > 1),
             "lsh_bands": bands, "lsh_rows": rows, "candidate_pairs": candidates,
             "p_candidate_at_threshold": round(candidate_probability(threshold, bands, rows), 3)}
    return cluster, stats


def shingle_matrix(songs, k: int = 5):
    """Binary song x shingle matrix (CSR) and the shingle count of every song."""
    vocab: dict[str, int] = {}
    rows, cols = [], []
    for i, s in enumerate(songs):
        for x in shingles(s.lyrics, k):
            rows.append(i)
            cols.append(vocab.setdefault(x, len(vocab)))
    X = sparse.csr_matrix((np.ones(len(rows), dtype=np.float32), (rows, cols)), shape=(len(songs), len(vocab)))
    return X, np.diff(X.indptr)


_TITLE_SEP = re.compile(r"\s+(?:x|vs\.?)\s+|\s*[+&/,|]\s*|\s+[-–]\s+", re.I)


def title_parts(title: str) -> list[str]:
    """Normalised parts of a title that may list several songs: "A x B", "A - B + C"."""
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", title or "")
    return [p for p in (norm_title(x) for x in _TITLE_SEP.split(t)) if p]


def find_compilations(songs, cluster_of, min_share: float = 0.1, min_shingles: int = 15,
                      max_cover: float = 0.8, max_clusters: int = 20) -> dict[str, list[str]]:
    """Medleys and mashups whose title lacks the words "mashup", "medley" or "liên khúc".

    The title must list several parts ("A x B", "A - B + C") and the lyrics must contain a large
    part of another cluster that one of those parts names: at least `min_share` of the song's
    shingles (and at least `min_shingles`), but less than `max_cover`, since a song made almost
    entirely of "A" is a version of "A", not a medley. Lyrics alone cannot tell a mashup from the
    songs it contains: "A" shares its chorus with every mashup of it just as the mashup shares it
    with "A". Shingles found in more than `max_clusters` clusters (refrains such as
    "la la la la la") are ignored. Returns doc_id -> the clusters named and borrowed from."""
    multi = [i for i, s in enumerate(songs) if len(title_parts(s.title)) >= 2]
    if not multi:
        return {}
    X, n_sh = shingle_matrix(songs)
    cl = sorted({cluster_of[s.doc_id] for s in songs})
    cidx = {c: k for k, c in enumerate(cl)}
    own = np.array([cidx[cluster_of[s.doc_id]] for s in songs])
    M = sparse.csr_matrix((np.ones(len(songs), dtype=np.float32), (np.arange(len(songs)), own)),
                          shape=(len(songs), len(cl)))
    Y = (X.T @ M).tocsr()                      # shingle x cluster
    Y.data[:] = 1.0
    common = np.diff(Y.indptr) > max_clusters
    Y = sparse.diags((~common).astype(np.float32)) @ Y
    O = (X[multi] @ Y).tocsr()                 # song x cluster: shingles of the song found there
    titles = defaultdict(set)
    for s in songs:
        titles[cidx[cluster_of[s.doc_id]]].add(norm_title(s.title))
    out = {}
    for row, i in enumerate(multi):
        parts = set(p for p in title_parts(songs[i].title) if len(p.split()) >= 2)
        lo, hi = O.indptr[row], O.indptr[row + 1]
        named = [cl[k] for k, v in zip(O.indices[lo:hi], O.data[lo:hi])
                 if k != own[i] and max(min_shingles, min_share * n_sh[i]) <= v < max_cover * n_sh[i]
                 and titles[k] & parts]
        if named:
            out[songs[i].doc_id] = sorted(named)
    return out
