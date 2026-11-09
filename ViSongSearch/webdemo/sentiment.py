"""Cảm xúc trong lời bài hát (TMG 03 Naive Bayes và phân tích cảm xúc, TMG 17 từ điển cảm xúc,
từ điển hàm ý; SEG 10.1 Sentiment).

1. Từ điển cảm xúc (lexicon/sentiment.json): mỗi từ một điểm trong [-1, 1]; từ phủ định
   ("không", "chẳng"...) đứng trước trong 3 âm tiết đảo dấu và giảm một nửa; từ nhấn mạnh
   ("rất", "quá", "lắm") nhân hệ số.
2. Từ điển cảm xúc theo loại (vui, buồn, yêu, nhớ, giận, sợ, hy vọng) và từ điển hàm ý
   (mưa, mùa thu, sân ga... có nghĩa đen trung tính nhưng gợi buồn).
3. Kết hợp với học máy: dùng từ điển gán nhãn yếu cho các dòng lời rõ cảm xúc, rồi huấn luyện
   Naive Bayes (tự cài, làm trơn Laplace) để gán nhãn cả những dòng không có từ nào trong từ điển.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from visong.text import clean

from .lexicons import PhraseMatcher, load


class SentimentLexicon:
    def __init__(self):
        lex = load("sentiment")
        self.scores = {**lex["positive"], **lex["negative"]}
        self.matcher = PhraseMatcher(self.scores)
        self.negators = PhraseMatcher(lex["negators"])
        self.intens = lex["intensifiers"]
        self.intens_matcher = PhraseMatcher(self.intens)
        emo = load("emotion")
        self.emotion_of: dict[str, list[str]] = defaultdict(list)
        for cat, words in emo.items():
            for w in words:
                self.emotion_of[w].append(cat)
        self.emo_matcher = PhraseMatcher(self.emotion_of)
        self.conn = load("connotation")
        self.conn_matcher = PhraseMatcher(self.conn)
        self.categories = list(emo)

    def score_line(self, text: str) -> dict:
        toks = clean(text).split()
        neg_ends = [j for _, j, _ in self.negators.find(toks)]
        intens = {(i, j): self.intens[w] for i, j, w in self.intens_matcher.find(toks)}
        hits, total = [], 0.0
        for i, j, w in self.matcher.find(toks):
            base = self.scores[w]
            val, notes = base, []
            if any(i - 3 <= e <= i for e in neg_ends):
                val = -0.5 * val
                notes.append("phủ định")
            for (a, b), f in intens.items():
                if b == i or a == j:            # "rất buồn", "buồn lắm"
                    val *= f
                    notes.append("nhấn mạnh")
                    break
            total += val
            hits.append({"word": w, "base": base, "value": round(val, 3), "notes": notes, "span": [i, j]})
        score = math.tanh(total)
        return {"score": round(score, 3), "label": label_of(score), "hits": hits}

    def emotions(self, text: str) -> Counter:
        toks = clean(text).split()
        c = Counter()
        for _, _, w in self.emo_matcher.find(toks):
            for cat in self.emotion_of[w]:
                c[cat] += 1
        return c

    def connotations(self, text: str) -> list[dict]:
        toks = clean(text).split()
        return [{"word": w, "polarity": self.conn[w][0], "note": self.conn[w][1]}
                for _, _, w in self.conn_matcher.find(toks)]

    def analyze_song(self, lyrics: str) -> dict:
        lines = [l for l in lyrics.split("\n") if l.strip()]
        rows = [self.score_line(l) for l in lines]
        scored = [r["score"] for r in rows if r["hits"]]
        conn = []
        for l in lines:
            conn.extend(self.connotations(l))
        conn_score = sum(c["polarity"] for c in conn) / max(1, len(lines))
        mean = sum(scored) / max(1, len(lines))
        overall = mean + 0.5 * conn_score
        emo = self.emotions(lyrics)
        return {"score": round(overall, 3), "label": label_of(overall, 0.06), "lines": len(lines),
                "lines_with_hits": len(scored), "emotions": dict(emo.most_common()),
                "connotation": dict(Counter(c["word"] for c in conn).most_common(6)),
                "top_pos": _top(rows, 1), "top_neg": _top(rows, -1)}


def _top(rows, sign: int) -> list[str]:
    words = Counter()
    for r in rows:
        for h in r["hits"]:
            if h["value"] * sign > 0:
                words[h["word"]] += 1
    return [w for w, _ in words.most_common(5)]


def label_of(score: float, th: float = 0.2) -> str:
    if score > th:
        return "vui"
    if score < -th:
        return "buồn"
    return "trung tính"


def features(text: str) -> list[str]:
    toks = clean(text).split()
    return toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]


class NaiveBayes:
    """Naive Bayes đa thức nhị phân (mỗi từ đếm một lần trong một dòng), làm trơn Laplace:
        ĉ = argmax_c  log P(c) + Σ_w log P(w | c),   P(w | c) = (count(w, c) + α) / (Σ count(·, c) + α|V|)"""

    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha

    def fit(self, docs: list[list[str]], labels: list[str]):
        self.classes = sorted(set(labels))
        n = Counter(labels)
        self.logprior = {c: math.log(n[c] / len(labels)) for c in self.classes}
        self.count = {c: Counter() for c in self.classes}
        for toks, y in zip(docs, labels):
            self.count[y].update(set(toks))
        self.vocab = set().union(*(set(c) for c in self.count.values()))
        V = len(self.vocab)
        self.total = {c: sum(self.count[c].values()) for c in self.classes}
        self.denom = {c: self.total[c] + self.alpha * V for c in self.classes}
        self.n_train = len(labels)
        return self

    def loglik(self, w: str, c: str) -> float:
        return math.log((self.count[c].get(w, 0) + self.alpha) / self.denom[c])

    def scores(self, toks: list[str]) -> dict[str, float]:
        toks = [w for w in set(toks) if w in self.vocab]
        return {c: self.logprior[c] + sum(self.loglik(w, c) for w in toks) for c in self.classes}

    def predict_proba(self, toks: list[str]) -> dict[str, float]:
        s = self.scores(toks)
        z = max(s.values())
        e = {c: math.exp(v - z) for c, v in s.items()}
        tot = sum(e.values())
        return {c: v / tot for c, v in e.items()}

    def predict(self, toks: list[str]) -> str:
        s = self.scores(toks)
        return max(s, key=s.get)

    def explain(self, text: str) -> dict:
        toks = [w for w in dict.fromkeys(features(text)) if w in self.vocab]
        rows = []
        for w in toks:
            rows.append({"w": w.replace("_", " "), **{c: round(self.loglik(w, c), 3) for c in self.classes}})
        return {"prior": {c: round(v, 3) for c, v in self.logprior.items()}, "words": rows,
                "scores": {c: round(v, 3) for c, v in self.scores(toks).items()},
                "proba": {c: round(v, 3) for c, v in self.predict_proba(toks).items()}}

    def top_features(self, k: int = 12, min_count: int = 5) -> dict[str, list[tuple[str, float]]]:
        out = {}
        for c in self.classes:
            others = [o for o in self.classes if o != c]
            cand = [(w, self.loglik(w, c) - max(self.loglik(w, o) for o in others))
                    for w, n in self.count[c].items() if n >= min_count]
            cand.sort(key=lambda x: -x[1])
            out[c] = [(w.replace("_", " "), round(s, 2)) for w, s in cand[:k]]
        return out


def weak_labels(lex: SentimentLexicon, lines: list[str], th: float = 0.35):
    """Dòng có |điểm từ điển| >= th được gán nhãn yếu 'tích cực' hoặc 'tiêu cực'."""
    docs, ys, unlabeled = [], [], []
    for l in lines:
        r = lex.score_line(l)
        if not r["hits"]:
            unlabeled.append(l)
        elif r["score"] >= th:
            docs.append(l)
            ys.append("tích cực")
        elif r["score"] <= -th:
            docs.append(l)
            ys.append("tiêu cực")
    return docs, ys, unlabeled
