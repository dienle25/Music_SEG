"""Mô hình ngôn ngữ n-gram và vector từ (TMG 02, 05, 07, 08).

- Độ hỗn loạn (perplexity) của một câu với mô hình 1-gram, 2-gram, 3-gram Kneser-Ney (TMG 02)
- Đoán âm tiết tiếp theo, sinh câu theo phân phối n-gram (Shannon game, TMG 02)
- Điền vào chỗ trống ___ bằng n-gram hai chiều: ý tưởng của masked LM (TMG 08) làm bằng n-gram
- Vector từ PPMI + SVD học từ đồng xuất hiện trong lời bài hát (TMG 05)
- Self-attention một đầu trên các vector từ đó, để thấy cơ chế của Transformer (TMG 07)
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import svds

from visong.diacritics import BOS, EOS
from visong.text import clean


class LMTools:
    def __init__(self, lm):
        self.lm = lm
        self.follow: dict[str, Counter] = defaultdict(Counter)
        self.follow2: dict[tuple, Counter] = defaultdict(Counter)
        self.precede: dict[str, Counter] = defaultdict(Counter)
        for (u, v, w), c in lm.c3.items():
            self.follow[v][w] += c
            self.follow2[(u, v)][w] += c
            self.precede[w][v] += c
        self.total = sum(lm.unigram.values())

    def p(self, order: int, u: str, v: str, w: str) -> float:
        if order >= 3:
            return self.lm.p3(u, v, w)
        if order == 2:
            return self.lm.p2(v, w)
        return (self.lm.unigram.get(w, 0) + 0.1) / (self.total + 0.1 * self.lm.vocab_size)

    def perplexity(self, text: str, order: int = 3) -> dict:
        toks = clean(text).split()
        seq = [BOS, BOS] + toks + [EOS]
        rows, logsum = [], 0.0
        for i in range(2, len(seq)):
            pr = self.p(order, seq[i - 2], seq[i - 1], seq[i])
            logsum += math.log(pr)
            rows.append({"w": seq[i], "p": pr, "logp": round(math.log(pr), 3)})
        n = max(1, len(rows))
        return {"order": order, "perplexity": round(math.exp(-logsum / n), 2), "tokens": rows,
                "logprob": round(logsum, 3)}

    def next_words(self, context: str, k: int = 10) -> list[tuple[str, float]]:
        toks = [BOS, BOS] + clean(context).split()
        u, v = toks[-2], toks[-1]
        pool = set(self.follow2.get((u, v), {})) | set(self.follow.get(v, {}))
        if not pool:
            pool = {w for w, _ in self.lm.unigram.most_common(200)}
        scored = sorted(((w, self.lm.p3(u, v, w)) for w in pool if w != EOS), key=lambda x: -x[1])
        return [(w, round(p, 4)) for w, p in scored[:k]]

    def generate(self, prefix: str = "", n: int = 12, seed: int = 0, top: int = 8) -> str:
        rng = random.Random(seed)
        toks = clean(prefix).split()
        for _ in range(n):
            cands = self.next_words(" ".join(toks), top)
            if not cands:
                break
            ws, ps = zip(*cands)
            toks.append(rng.choices(ws, weights=ps)[0])
        return " ".join(toks)

    def fill_mask(self, text: str, k: int = 10) -> dict:
        """Câu có một chỗ trống '___' hoặc [MASK]: chấm điểm mỗi âm tiết w bằng xác suất
        của cả câu khi đặt w vào chỗ trống (nhìn được cả hai phía, như masked LM)."""
        t = text.replace("[MASK]", " ___ ").replace("[mask]", " ___ ")
        parts = t.split("___")
        if len(parts) != 2:
            return {"error": "Câu cần đúng một chỗ trống ___"}
        left, right = clean(parts[0]).split(), clean(parts[1]).split()
        seq_l = [BOS, BOS] + left
        pool = set(self.follow.get(seq_l[-1], {}))
        if right:
            pool |= set(self.precede.get(right[0], {}))
        pool = {w for w in pool if w not in (BOS, EOS)} or {w for w, _ in self.lm.unigram.most_common(300)}
        scored = []
        for w in pool:
            seq = seq_l + [w] + right + [EOS]
            i0 = len(seq_l)
            s = sum(math.log(self.lm.p3(seq[i - 2], seq[i - 1], seq[i]))
                    for i in range(i0, min(len(seq), i0 + 3)))
            scored.append((w, s))
        scored.sort(key=lambda x: -x[1])
        top = scored[:k]
        if not top:
            return {"left": " ".join(left), "right": " ".join(right), "candidates": []}
        z = max(s for _, s in top)
        probs = np.exp(np.array([s - z for _, s in top]))
        probs /= probs.sum()
        return {"left": " ".join(left), "right": " ".join(right),
                "candidates": [{"w": w, "score": round(s, 3), "p": round(float(p), 3)}
                               for (w, s), p in zip(top, probs)]}


# ------------------------------------------------------------------ vector từ (TMG 05)

class Embeddings:
    """Ma trận đồng xuất hiện âm tiết trong cửa sổ ±window (trong một dòng lời), PPMI có làm trơn
    ngữ cảnh mũ alpha, rồi SVD rút gọn còn dim chiều. Vector mỗi âm tiết = U·sqrt(S), chuẩn hóa."""

    def __init__(self, texts, window: int = 2, min_count: int = 5, dim: int = 64, alpha: float = 0.75):
        counts = Counter()
        lines = []
        for t in texts:
            for line in t.split("\n"):
                toks = clean(line).split()
                if toks:
                    lines.append(toks)
                    counts.update(toks)
        self.vocab = [w for w, c in counts.most_common() if c >= min_count]
        self.index = {w: i for i, w in enumerate(self.vocab)}
        self.count = counts
        pairs = Counter()
        for toks in lines:
            ids = [self.index.get(w, -1) for w in toks]
            for i, a in enumerate(ids):
                if a < 0:
                    continue
                for j in range(max(0, i - window), min(len(ids), i + window + 1)):
                    if j != i and ids[j] >= 0:
                        pairs[(a, ids[j])] += 1
        V = len(self.vocab)
        rows = np.fromiter((a for a, _ in pairs), np.int64, len(pairs))
        cols = np.fromiter((b for _, b in pairs), np.int64, len(pairs))
        vals = np.fromiter(pairs.values(), np.float64, len(pairs))
        C = sparse.csr_matrix((vals, (rows, cols)), shape=(V, V))
        total = C.sum()
        pw = np.asarray(C.sum(axis=1)).ravel() / total
        pc = np.asarray(C.sum(axis=0)).ravel() ** alpha
        pc /= pc.sum()
        coo = C.tocoo()
        pmi = np.log((coo.data / total) / (pw[coo.row] * pc[coo.col]))
        keep = pmi > 0
        self.ppmi = sparse.csr_matrix((pmi[keep], (coo.row[keep], coo.col[keep])), shape=(V, V))
        k = min(dim, V - 2)
        U, S, _ = svds(self.ppmi, k=k)
        order = np.argsort(-S)
        W = U[:, order] * np.sqrt(S[order])
        W /= np.linalg.norm(W, axis=1, keepdims=True) + 1e-9
        self.W = W.astype(np.float32)
        self.dim = k
        self.stats = {"vocab": V, "pairs": int(len(pairs)), "nonzero_ppmi": int(self.ppmi.nnz), "dim": k,
                      "window": window, "alpha": alpha}

    def vec(self, w: str):
        i = self.index.get(w)
        return None if i is None else self.W[i]

    def nearest(self, w: str, k: int = 10) -> list[tuple[str, float]]:
        v = self.vec(clean(w))
        if v is None:
            return []
        sims = self.W @ v
        idx = np.argsort(-sims)[: k + 1]
        return [(self.vocab[i], round(float(sims[i]), 3)) for i in idx if self.vocab[i] != clean(w)][:k]

    def similarity(self, a: str, b: str) -> float | None:
        va, vb = self.vec(clean(a)), self.vec(clean(b))
        if va is None or vb is None:
            return None
        return round(float(va @ vb), 3)

    def analogy(self, a: str, b: str, c: str, k: int = 5) -> list[tuple[str, float]]:
        """a : b = c : ?  (vector b - a + c)"""
        vs = [self.vec(clean(x)) for x in (a, b, c)]
        if any(v is None for v in vs):
            return []
        q = vs[1] - vs[0] + vs[2]
        q /= np.linalg.norm(q) + 1e-9
        sims = self.W @ q
        bad = {clean(a), clean(b), clean(c)}
        idx = np.argsort(-sims)[: k + 3]
        return [(self.vocab[i], round(float(sims[i]), 3)) for i in idx if self.vocab[i] not in bad][:k]

    def text_vector(self, text: str):
        vs = [self.vec(w) for w in clean(text).split()]
        vs = [v for v in vs if v is not None]
        if not vs:
            return None
        v = np.mean(vs, axis=0)
        return v / (np.linalg.norm(v) + 1e-9)


def positional_encoding(n: int, d: int) -> np.ndarray:
    pos = np.arange(n)[:, None]
    i = np.arange(d)[None, :]
    angle = pos / np.power(10000, (2 * (i // 2)) / d)
    return np.where(i % 2 == 0, np.sin(angle), np.cos(angle))


def self_attention(text: str, emb: Embeddings, pe_scale: float = 0.3) -> dict:
    """Attention(Q, K, V) = softmax(Q·Kᵀ / sqrt(d)) · V với Q = K = V = X (vector từ + mã hóa vị trí).
    Dùng W_Q = W_K = W_V = I để dễ giải thích: âm tiết chú ý tới âm tiết có nghĩa gần nó."""
    toks = clean(text).split()[:16]
    if not toks:
        return {"tokens": [], "weights": []}
    d = emb.dim
    X = np.stack([emb.vec(w) if emb.vec(w) is not None else np.zeros(d, np.float32) for w in toks]).astype(np.float64)
    X = X + pe_scale * positional_encoding(len(toks), d) / math.sqrt(d / 2)
    scores = X @ X.T / math.sqrt(d) * 8.0          # nhân 8 cho phân phối rõ hơn (nhiệt độ thấp)
    scores -= scores.max(axis=1, keepdims=True)
    A = np.exp(scores)
    A /= A.sum(axis=1, keepdims=True)
    return {"tokens": toks, "weights": np.round(A, 3).tolist(),
            "oov": [w for w in toks if emb.vec(w) is None]}
