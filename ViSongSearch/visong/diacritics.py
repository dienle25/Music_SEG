"""Diacritic restoration with a syllable trigram language model (TMG 02, SEG 6).

A query typed without marks ("o sau bo vai") is decoded as a noisy channel:
each plain syllable can come from any marked syllable that strips to it
("bo" -> bờ, bố, bỏ, bộ, ...), and an interpolated Kneser-Ney trigram LM trained
on lyrics picks the most probable sequence with a beam search.

Telex leftovers ("nhuwngx") are decoded into their toneless form plus tone and
matched against the vocabulary.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from .noise import untelex
from .text import clean, has_diacritics, remove_diacritics, remove_tones, tone_of

BOS, EOS = "<s>", "</s>"


class KneserNeyTrigram:
    def __init__(self, sentences, discount: float = 0.75):
        self.D = discount
        c3 = Counter()
        for toks in sentences:
            seq = [BOS, BOS] + toks + [EOS]
            for i in range(2, len(seq)):
                c3[(seq[i - 2], seq[i - 1], seq[i])] += 1
        self.c3 = c3
        self.ctx3 = Counter()
        self.types3 = Counter()
        bigram_types = Counter()                  # N1+(., v, w)
        for (u, v, w), c in c3.items():
            self.ctx3[(u, v)] += c
            self.types3[(u, v)] += 1
            bigram_types[(v, w)] += 1
        self.n2 = bigram_types
        self.ctx2 = Counter()
        self.types2 = Counter()
        uni_types = Counter()                     # N1+(., w) over bigram types
        for (v, w), c in bigram_types.items():
            self.ctx2[v] += c
            self.types2[v] += 1
            uni_types[w] += 1
        self.n1 = uni_types
        self.n1_total = sum(uni_types.values())
        self.vocab_size = len(uni_types) + 1
        self.unigram = Counter()
        for (u, v, w), c in c3.items():
            self.unigram[w] += c

    def p1(self, w: str) -> float:
        return (self.n1.get(w, 0) + 0.1) / (self.n1_total + 0.1 * self.vocab_size)

    def p2(self, v: str, w: str) -> float:
        ctx = self.ctx2.get(v, 0)
        if ctx == 0:
            return self.p1(w)
        return (max(self.n2.get((v, w), 0) - self.D, 0) / ctx
                + self.D * self.types2[v] / ctx * self.p1(w))

    def p3(self, u: str, v: str, w: str) -> float:
        ctx = self.ctx3.get((u, v), 0)
        if ctx == 0:
            return self.p2(v, w)
        return (max(self.c3.get((u, v, w), 0) - self.D, 0) / ctx
                + self.D * self.types3[(u, v)] / ctx * self.p2(v, w))

    def logp(self, u: str, v: str, w: str) -> float:
        return math.log(self.p3(u, v, w))


class DiacriticRestorer:
    def __init__(self, lyrics_texts, max_candidates: int = 8, beam: int = 8, order: int = 3):
        """`order` 3 is the trigram LM; 2 and 1 are for the ablation (1 = most frequent form)."""
        sentences = []
        for text in lyrics_texts:
            for line in text.split("\n"):
                toks = clean(line).split()
                if toks:
                    sentences.append(toks)
        self.lm = KneserNeyTrigram(sentences)
        self.by_plain = defaultdict(Counter)
        self.by_toneless = defaultdict(Counter)
        for (u, v, w), c in self.lm.c3.items():
            if w in (EOS,):
                continue
            self.by_plain[remove_diacritics(w)][w] += c
            self.by_toneless[remove_tones(w)][w] += c
        self.max_candidates = max_candidates
        self.beam = beam
        self.order = order
        self.n_tokens = sum(self.lm.unigram.values())

    def logp(self, u: str, v: str, w: str) -> float:
        if self.order >= 3:
            return self.lm.logp(u, v, w)
        if self.order == 2:
            return math.log(self.lm.p2(v, w))
        # unigram: relative frequency, so each syllable takes its most frequent marked form
        return math.log((self.lm.unigram.get(w, 0) + 0.1) / (self.n_tokens + 0.1 * self.lm.vocab_size))

    def candidates(self, tok: str) -> list[str]:
        if has_diacritics(tok):
            return [tok]
        cands = [w for w, _ in self.by_plain.get(tok, Counter()).most_common(self.max_candidates)]
        if cands:
            return cands
        decoded = untelex(tok)
        if decoded:
            base, tone = decoded
            pool = self.by_toneless.get(base, Counter())
            cands = [w for w, _ in pool.most_common() if tone_of(w) == tone][: self.max_candidates]
            if cands:
                return cands
        return [tok]

    def restore(self, query: str) -> str:
        toks = clean(query).split()
        if not toks:
            return ""
        beams = [((BOS, BOS), 0.0, [])]
        for tok in toks:
            new = {}
            for (u, v), score, path in beams:
                for c in self.candidates(tok):
                    s = score + self.logp(u, v, c)
                    key = (v, c)
                    if key not in new or new[key][0] < s:
                        new[key] = (s, path + [c])
            beams = sorted(((k, s, p) for k, (s, p) in new.items()), key=lambda x: -x[1])[: self.beam]
        best = max(beams, key=lambda b: b[1] + self.logp(b[0][0], b[0][1], EOS))
        return " ".join(best[2])


def syllable_accuracy(pred: str, gold: str) -> tuple[int, int]:
    p, g = clean(pred).split(), clean(gold).split()
    n = min(len(p), len(g))
    return sum(p[i] == g[i] for i in range(n)), len(g)
