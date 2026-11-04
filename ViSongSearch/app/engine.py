"""Search engine used by the web demo: index loading, query processing, ranking, snippets.

The ranking is the configuration evaluated in the paper (visong/pipeline.py, fusion "lexall"):
RRF over four lexical lists (restored query on syllable uni+bigrams, unmarked uni+bigrams,
character trigrams, title/artist/genre field) with the weights tuned on the dev queries, and
per predicted query type where cross-validation kept them. With a dense model, the weights of
the matching hybrid run are used instead, and the dense list is added last.
"""
from __future__ import annotations

import json
import os
import re
import time

from visong.corpus import credits, load_corpus, read_jsonl
from visong.diacritics import DiacriticRestorer
from visong.fusion import rrf
from visong.lexical import LexicalIndex
from visong.qtype import QueryTypeClassifier
from visong.queries import QUERY_FILES
from visong.text import clean, has_diacritics, remove_diacritics

DEFAULT_WEIGHTS = (1.0, 0.0, 0.0, 0.0)          # what dev tuning chose on the pilot


def meta_text(s) -> str:
    return " ".join(x for x in (s.title, s.artist, s.extra.get("singers", ""), s.genre) if x)


class Engine:
    def __init__(self, work: str, dense_name: str | None = None, dense_model: str | None = None):
        t0 = time.time()
        self.work = work
        self.songs = {s.doc_id: s for s in load_corpus(os.path.join(work, "corpus.jsonl"))}
        ids = list(self.songs)
        texts = [self.songs[d].lyrics for d in ids]

        def load_json(name):
            path = os.path.join(work, "results", name)
            return json.load(open(path)) if os.path.exists(path) else {}
        params, fusion = load_json("lexical_params.json"), load_json("fusion_params.json")

        def kb(name, default=(0.9, 0.4)):
            p = params.get(name)
            return (p["k1"], p["b"]) if p and "k1" in p else default
        self.syl12 = LexicalIndex(texts, ids, "syl12", *kb("bm25_syl12"))
        self.nodiac12 = LexicalIndex(texts, ids, "nodiac12", *kb("bm25_nodiac12"))
        self.char3 = LexicalIndex(texts, ids, "char3", *kb("bm25_char3"))
        self.meta = LexicalIndex([meta_text(self.songs[d]) for d in ids], ids, "nodiac12", *kb("bm25_meta"))
        self.restorer = DiacriticRestorer(texts)

        # query-type classifier, trained as in the pipeline: rule queries of training songs
        # plus the dev paraphrases and descriptions, with the class weighting the pipeline chose
        self.qtype = None
        qs = []
        for f in QUERY_FILES:
            path = os.path.join(work, "queries", f)
            if os.path.exists(path):
                qs += read_jsonl(path)
        fit = [q for q in qs if (q["split"] == "train" and q["type"] in ("Q1", "Q2", "Q3"))
               or (q["split"] == "dev" and q["type"] in ("Q4", "Q5", "Q4u", "Q5u"))]
        if len({q["type"] for q in fit}) >= 2:
            balanced = bool(params.get("qtype_logreg", {}).get("balanced", False))
            self.qtype = QueryTypeClassifier(set(self.syl12.vocab), set(self.nodiac12.vocab), "logreg", balanced).fit(
                [q["text"] for q in fit], [q["type"] for q in fit])

        self.passages = {}
        for p in read_jsonl(os.path.join(work, "passages.jsonl")):
            self.passages.setdefault(p["doc_id"], []).append(p["text"])
        self.dense = self.encoder = None
        config = fusion.get("lexall")
        if dense_name and dense_model:
            from visong.dense import DenseEncoder, load_index
            self.dense = load_index(work, dense_name)
            self.encoder = DenseEncoder(dense_model)
            config = fusion.get(f"hybrid_{dense_name}", config)
        self.global_weights = tuple(config["global"]["weights"]) if config else DEFAULT_WEIGHTS
        self.type_weights = {t: tuple(v["weights"]) for t, v in (config or {}).get("per_type", {}).items()
                             if v.get("use_type")}
        if self.dense is not None and len(self.global_weights) == 4:
            self.global_weights += (1.0,)                 # no tuned hybrid yet: give the dense list weight 1
        self.load_seconds = time.time() - t0

    def weights_for(self, qtype: str) -> tuple:
        return self.type_weights.get(qtype, self.global_weights)

    def search(self, query: str, k: int = 10) -> dict:
        t0 = time.time()
        q = clean(query)
        if not q:
            return {"query": query, "results": [], "restored": "", "qtype": None, "ms": 0.0}
        restored = self.restorer.restore(q) if not has_diacritics(q) else q
        qtype = self.qtype.predict([q])[0] if self.qtype else "Q1"
        lists = [self.syl12.search(restored, 100), self.nodiac12.search(q, 100), self.char3.search(q, 100),
                 self.meta.search(q, 100)]
        if self.dense is not None:
            lists.append(self.dense.search_batch(self.encoder.encode_queries([q]), 100)[0])
        weights = list(self.weights_for(qtype))
        if not any(weights[:len(lists)]):
            weights = [1.0] * len(lists)
        fused = rrf(lists, weights=weights[:len(lists)])[:k]
        if not fused:                                   # nothing matched exactly: fall back to equal weights
            fused = rrf(lists)[:k]
        results = []
        for rank, (d, score) in enumerate(fused, 1):
            s = self.songs[d]
            results.append({"rank": rank, "doc_id": d, "title": s.title,
                            "artist": ", ".join(credits(s).split(", ")[:3]),
                            "source": s.source, "url": s.url, "score": score,
                            "snippet": self.snippet(d, q)})
        return {"query": query, "restored": restored if restored != q else "", "qtype": qtype,
                "weights": weights[:len(lists)], "results": results, "ms": 1000 * (time.time() - t0)}

    def snippet(self, doc_id: str, query: str) -> str:
        """Best passage by overlap of diacritic-free syllables, with matches in <mark>."""
        qset = set(remove_diacritics(clean(query)).split())
        best, best_score = "", -1.0
        for text in self.passages.get(doc_id, []):
            toks = remove_diacritics(clean(text)).split()
            score = sum(t in qset for t in toks) / (len(toks) ** 0.3 or 1)
            if score > best_score:
                best, best_score = text, score
        out = []
        for line in best.split("\n"):
            words = []
            for w in line.split():
                plain = remove_diacritics(clean(w))
                words.append(f"<mark>{escape(w)}</mark>" if plain and plain in qset else escape(w))
            out.append(" ".join(words))
        return " / ".join(out)


def escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


_WS = re.compile(r"\s+")
