"""Các mô hình truy hồi bổ sung (SEG 7.1 mô hình không gian vector; SEG 7.6 chủ đề ẩn và lệch từ vựng;
TMG 05 vector tf-idf, ma trận từ-văn bản).

- VSM: vector tf-idf (log tf, idf, chuẩn hóa cosine) trên âm tiết không dấu và cặp âm tiết kề nhau
- LSA: phân rã SVD rút gọn của ma trận tf-idf (150 chiều); truy vấn được chiếu vào cùng không gian,
  nên hai bài dùng từ khác nhau nhưng cùng chủ đề vẫn gần nhau
"""
from __future__ import annotations

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer

from visong.text import clean, remove_diacritics


def nodiac12(text: str) -> list[str]:
    toks = remove_diacritics(clean(text)).split()
    return toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]


class VectorSpace:
    def __init__(self, texts: list[str], doc_ids: list[str], lsa_dim: int = 150, seed: int = 0):
        self.doc_ids = doc_ids
        self.vec = TfidfVectorizer(analyzer=nodiac12, sublinear_tf=True, min_df=2, max_features=40000, norm="l2")
        self.X = self.vec.fit_transform(texts)
        k = min(lsa_dim, self.X.shape[1] - 1, self.X.shape[0] - 1)
        self.svd = TruncatedSVD(n_components=k, random_state=seed)
        Z = self.svd.fit_transform(self.X)
        self.svd.components_ = self.svd.components_.astype(np.float32)
        self.Z = Z / (np.linalg.norm(Z, axis=1, keepdims=True) + 1e-9)
        self.terms = np.array(self.vec.get_feature_names_out())
        self.explained = float(self.svd.explained_variance_ratio_.sum())

    def tfidf_scores(self, query: str) -> np.ndarray:
        q = self.vec.transform([query])
        return np.asarray((self.X @ q.T).todense()).ravel()

    def lsa_query(self, query: str):
        z = self.svd.transform(self.vec.transform([query]))[0]
        n = np.linalg.norm(z)
        return z / n if n else z

    def lsa_scores(self, query: str) -> np.ndarray:
        return self.Z @ self.lsa_query(query)

    def explain_tfidf(self, query: str, doc: int, k: int = 8) -> list[tuple[str, float]]:
        q = self.vec.transform([query])
        prod = self.X[doc].multiply(q)
        idx = prod.nonzero()[1]
        rows = sorted(((self.terms[j].replace("_", " "), float(prod[0, j])) for j in idx), key=lambda x: -x[1])
        return [(t, round(v, 4)) for t, v in rows[:k]]

    def keywords(self, doc: int, k: int = 8) -> list[str]:
        row = self.X[doc]
        idx = row.nonzero()[1]
        best = sorted(idx, key=lambda j: -row[0, j])[:k]
        return [self.terms[j].replace("_", " ") for j in best]

    def similar(self, doc: int, k: int = 6) -> list[tuple[int, float]]:
        sims = self.Z @ self.Z[doc]
        idx = np.argsort(-sims)[: k + 1]
        return [(int(i), float(sims[i])) for i in idx if i != doc][:k]
