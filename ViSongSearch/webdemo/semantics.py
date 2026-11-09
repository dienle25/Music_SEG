"""Nghĩa của từ (TMG 16 Word Senses and WordNet) và mở rộng truy vấn (SEG 6.2 Query expansion).

- WordNet thu nhỏ (lexicon/wordnet.json): synset, quan hệ is-a, trái nghĩa, bộ phận
- Khử nhập nhằng nghĩa bằng thuật toán Lesk rút gọn: chọn nghĩa có định nghĩa và ví dụ
  trùng nhiều từ nhất với ngữ cảnh
- Mở rộng truy vấn bằng các từ đồng nghĩa của nghĩa đã chọn (trọng số 0.3)
- Liên kết từ (term association) trên kho: Dice, MIM, EMIM, chi bình phương (SEG 6.2)
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from visong.text import clean, remove_diacritics

from .lexicons import PhraseMatcher, load, stopwords


class WordNet:
    def __init__(self):
        self.synsets: dict = load("wordnet")["synsets"]
        self.by_lemma: dict[str, list[str]] = defaultdict(list)
        self.hyponyms: dict[str, list[str]] = defaultdict(list)
        self.parts: dict[str, list[str]] = defaultdict(list)
        for sid, s in self.synsets.items():
            for lem in s["lemmas"]:
                key = clean(lem)
                if sid not in self.by_lemma[key]:
                    self.by_lemma[key].append(sid)
            if s.get("hypernym"):
                self.hyponyms[s["hypernym"]].append(sid)
            if s.get("part_of"):
                self.parts[s["part_of"]].append(sid)
        self.matcher = PhraseMatcher(list(self.by_lemma))

    def senses(self, word: str) -> list[str]:
        return list(self.by_lemma.get(clean(word), []))

    def signature(self, sid: str) -> set[str]:
        s = self.synsets[sid]
        words = " ".join([s["gloss"]] + s.get("examples", []) + s["lemmas"])
        return {w for w in clean(words).split() if w not in stopwords()}

    def lesk(self, word: str, context: list[str]) -> dict:
        senses = self.senses(word)
        own = set(clean(word).split())
        ctx = {w for w in context if w not in stopwords() and w not in own}
        rows = []
        for sid in senses:
            overlap = sorted(self.signature(sid) & ctx)
            rows.append({"synset": sid, "gloss": self.synsets[sid]["gloss"], "overlap": overlap,
                         "score": len(overlap)})
        best = max(rows, key=lambda r: r["score"]) if rows else None     # hòa: nghĩa đầu (phổ biến)
        return {"word": word, "senses": rows, "best": best["synset"] if best else None}

    def hypernym_path(self, sid: str) -> list[str]:
        path, seen = [sid], {sid}
        while self.synsets.get(path[-1], {}).get("hypernym"):
            nxt = self.synsets[path[-1]]["hypernym"]
            if nxt in seen or nxt not in self.synsets:
                break
            path.append(nxt)
            seen.add(nxt)
        return path

    def path_similarity(self, a: str, b: str) -> float:
        pa, pb = self.hypernym_path(a), self.hypernym_path(b)
        common = [x for x in pa if x in pb]
        if not common:
            return 0.0
        c = common[0]
        return round(1.0 / (1 + pa.index(c) + pb.index(c)), 3)

    def relations(self, sid: str) -> dict:
        s = self.synsets[sid]
        return {"synset": sid, "lemmas": s["lemmas"], "gloss": s["gloss"], "examples": s.get("examples", []),
                "hypernym_path": self.hypernym_path(sid), "hyponyms": self.hyponyms.get(sid, []),
                "antonym": s.get("antonym"), "part_of": s.get("part_of"), "parts": self.parts.get(sid, [])}

    def analyze(self, text: str) -> list[dict]:
        """Mọi từ có trong WordNet của câu, với nghĩa Lesk chọn và từ đồng nghĩa để mở rộng."""
        toks = clean(text).split()
        out = []
        for i, j, lemma in self.matcher.find(toks):
            res = self.lesk(lemma, toks[:i] + toks[j:])
            sid = res["best"]
            syn = [l for l in self.synsets[sid]["lemmas"] if clean(l) != clean(lemma)] if sid else []
            out.append({"word": lemma, "span": [i, j], "ambiguous": len(res["senses"]) > 1,
                        "sense": sid, "gloss": self.synsets[sid]["gloss"] if sid else "",
                        "senses": res["senses"], "synonyms": syn,
                        "antonym": self.synsets[sid].get("antonym") if sid else None})
        return out

    def expansion(self, text: str, weight: float = 0.3, max_terms: int = 8) -> tuple[str, float, list[dict]]:
        """Chuỗi từ thêm vào truy vấn và trọng số của nó."""
        info = self.analyze(text)
        added, seen = [], set(clean(text).split())
        for item in info:
            for s in item["synonyms"]:
                for w in [s]:
                    if clean(w) not in seen:
                        added.append(clean(w))
                        seen.add(clean(w))
        return " ".join(added[:max_terms]), weight, info


class TermAssociation:
    """Độ liên kết giữa hai từ dựa trên số bài cùng chứa (n_ab), số bài chứa mỗi từ (n_a, n_b), N bài."""

    def __init__(self, index, min_df: int = 5):
        self.ix = index
        B = (index.tf > 0).astype(np.float32).tocsc()
        self.B = B
        self.Bcsr = B.tocsr()
        self.df = np.asarray(B.sum(axis=0)).ravel()
        self.N = B.shape[0]
        self.terms = [None] * len(index.vocab)
        for t, j in index.vocab.items():
            self.terms[j] = t
        self.ok = (self.df >= min_df)
        stop = stopwords()
        for j, t in enumerate(self.terms):
            if any(x in stop for x in t.split("_")):
                self.ok[j] = False

    def related(self, term: str, k: int = 10) -> dict:
        toks = self.ix.analyze(term)
        if not toks:
            return {}
        t = toks[0] if len(toks) == 1 else "_".join(clean(term).split()[:2])
        j = self.ix.vocab.get(t)
        if j is None:
            return {"term": term, "missing": True}
        docs = self.B[:, j].nonzero()[0]
        nab = np.asarray(self.Bcsr[docs].sum(axis=0)).ravel()
        na, nb, N = float(self.df[j]), self.df, float(self.N)
        mask = self.ok & (nab > 0)
        mask[j] = False
        with np.errstate(divide="ignore", invalid="ignore"):
            dice = np.where(mask, 2 * nab / (na + nb), 0)
            mim = np.where(mask, nab / (na * nb), 0)
            emim = np.where(mask, nab * np.log(N * nab / (na * nb)), 0)
            e = na * nb / N
            chi = np.where(mask, (nab - e) ** 2 / e, 0)
        out = {"term": t, "n_a": int(na), "N": int(N)}
        for name, arr in (("dice", dice), ("mim", mim), ("emim", emim), ("chi2", chi)):
            idx = np.argsort(-arr)[:k]
            out[name] = [{"term": self.terms[i].replace("_", " "), "score": round(float(arr[i]), 4),
                          "n_ab": int(nab[i])} for i in idx if arr[i] > 0]
        return out


def plain(text: str) -> str:
    return remove_diacritics(clean(text))


def cosine(a, b) -> float:
    na, nb = math.sqrt(float(a @ a)), math.sqrt(float(b @ b))
    return float(a @ b) / (na * nb) if na and nb else 0.0
