"""N-best diacritic restoration for retrieval (round-1 method).

`DiacriticRestorer.restore` keeps the single most probable marked form of a query. When the language
model is unsure, for instance on a song it has not seen, the right form is often the second or third
hypothesis, and one wrong syllable breaks the two syllable bigrams around it. Here the same beam search
keeps the k best paths of every language-model state, so it returns the k most probable restorations
with their log-probabilities; the first is the restoration `restore` returns, because the best path of
every state, and with it the pruning of states, is unchanged.

Every restoration is searched, and the songs are scored with the restorations weighted by their
posterior under the language model. The channel is deterministic (every candidate strips to the typed
syllable), so P(r | q) is proportional to P_LM(r); `posterior` normalises over the n-best list and
sharpens (alpha > 1) or flattens (alpha < 1) it. Two fusions:
  - weighted RRF: sum_i p_i / (60 + rank_i(d)) over the top 100 of each restoration;
  - score mixture: sum_i p_i * BM25(r_i, d), the posterior-expected BM25 score. BM25 is linear in the
    query term counts, so this is BM25 of one query whose counts are the weighted counts of all
    restorations (`mixture_query`).
`evidence_weights` adds an optional retrieval-aware factor (top BM25 score of each restoration, relative
to the best one, to the power beta): a restoration that matches some song's lyrics well gains weight.
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from .diacritics import BOS, EOS
from .text import clean


def nbest(restorer, query: str, k: int = 5) -> list[tuple[str, float]]:
    """The k most probable restorations of `query` with their log-probabilities (best first).

    Beam search over the states (u, v) of the trigram model, as in DiacriticRestorer.restore: at
    every syllable the `restorer.beam` states with the best path survive, but each state keeps its k
    best paths instead of one. Ties keep the order in which paths were found, as `restore` does, so
    nbest(...)[0][0] == restorer.restore(query)."""
    toks = clean(query).split()
    if not toks:
        return [("", 0.0)]
    states: dict = {(BOS, BOS): [(0.0, ())]}
    for tok in toks:
        cands = restorer.candidates(tok)
        new: dict = {}
        for (u, v), paths in states.items():
            for c in cands:
                lp = restorer.logp(u, v, c)
                new.setdefault((v, c), []).extend((s + lp, path + (c,)) for s, path in paths)
        pruned = []
        for key, lst in new.items():
            lst.sort(key=lambda x: -x[0])                     # stable: first found wins ties
            pruned.append((key, lst[:k]))
        pruned.sort(key=lambda kv: -kv[1][0][0])              # states ranked by their best path
        states = dict(pruned[: restorer.beam])
    finals = []
    for (u, v), paths in states.items():
        end = restorer.logp(u, v, EOS)
        finals.extend((s + end, path) for s, path in paths)
    finals.sort(key=lambda x: -x[0])
    return [(" ".join(p), float(s)) for s, p in finals[:k]]


def posterior(logps, alpha: float = 1.0) -> np.ndarray:
    """Weights proportional to P_LM(r)^alpha over the n-best list (alpha = 0: uniform)."""
    x = alpha * np.asarray(logps, float)
    x = x - x.max()
    w = np.exp(x)
    return w / w.sum()


def evidence_weights(logps, tops, alpha: float = 1.0, beta: float = 0.0) -> np.ndarray:
    """Posterior weights times (top BM25 score / best top score)^beta, renormalised. beta = 0 gives
    `posterior`; with beta > 0 a restoration that matches no song gets no weight."""
    w = posterior(logps, alpha)
    tops = np.asarray(tops, float)
    if beta == 0 or tops.max() <= 0:
        return w
    w = w * (np.maximum(tops, 0) / tops.max()) ** beta
    return w / w.sum()


def mixture_query(index, restorations, weights) -> dict[int, float]:
    """Query vector of the score mixture: term counts of every restoration, weighted."""
    qvec: dict[int, float] = {}
    for text, w in zip(restorations, weights):
        if w == 0:
            continue
        for term, c in Counter(index.analyze(text)).items():
            j = index.vocab.get(term)
            if j is not None:
                qvec[j] = qvec.get(j, 0.0) + float(w) * c
    return qvec


def mixture_scores(index, restorations, weights) -> np.ndarray:
    """sum_i w_i * BM25(r_i, d) for every song d."""
    return index.bm25_scores(mixture_query(index, restorations, weights))
