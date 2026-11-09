"""Phân cụm văn bản (TMG 06 Clustering for Text Similarity; SEG 10.2 Clustering; SEG 6.3 phân cụm kết quả).

- Độ đo khoảng cách: Euclid, cosine, Jaccard, Pearson giữa hai bài
- K-means tự cài (in nhãn từng vòng lặp) và K-means của sklearn trên vector LSA đã chuẩn hóa
- Phân cụm phân cấp gộp dần (average link) trên một mẫu nhỏ, in từng bước gộp
- DBSCAN: điểm lõi / biên / nhiễu
- Chọn K theo silhouette và tổng bình phương trong cụm (elbow)
- Đặt tên cụm bằng các từ tf-idf nổi bật nhất của tâm cụm
"""
from __future__ import annotations

import numpy as np
from sklearn.cluster import DBSCAN, AgglomerativeClustering, KMeans
from sklearn.metrics import silhouette_score

import re

from visong.text import remove_diacritics

from .lexicons import stopwords

_VI_SYL = re.compile(r"^(ngh|ng|nh|ch|tr|th|ph|kh|gh|gi|qu|[bcdghklmnpqrstvx])?(uy|[aeiouy])+(ng|nh|ch|[cmnpt]|[iouy])?$")


def vietnamese_syllable(t: str) -> bool:
    return bool(_VI_SYL.match(remove_diacritics(t).replace("đ", "d")))


def distances(a_vec, b_vec, a_set: set, b_set: set) -> dict:
    a, b = np.asarray(a_vec, float), np.asarray(b_vec, float)
    cos = float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1))
    pear = float(np.corrcoef(a, b)[0, 1]) if a.std() and b.std() else 0.0
    jac = len(a_set & b_set) / max(1, len(a_set | b_set))
    return {"euclid": round(float(np.linalg.norm(a - b)), 4), "cosine_sim": round(cos, 4),
            "cosine_dist": round(1 - cos, 4), "jaccard": round(jac, 4), "pearson": round(pear, 4)}


def kmeans_manual(X: np.ndarray, k: int, iters: int = 20, seed: int = 0) -> dict:
    """K-means đúng như slide: chọn tâm ngẫu nhiên, gán điểm vào tâm gần nhất, tính lại tâm, lặp đến hội tụ."""
    rng = np.random.default_rng(seed)
    C = X[rng.choice(len(X), k, replace=False)]
    history, labels = [], None
    for it in range(iters):
        d = ((X[:, None, :] - C[None, :, :]) ** 2).sum(-1)
        new = d.argmin(1)
        sse = float(d[np.arange(len(X)), new].sum())
        changed = int((new != labels).sum()) if labels is not None else len(X)
        history.append({"iter": it + 1, "sse": round(sse, 4), "changed": changed,
                        "sizes": np.bincount(new, minlength=k).tolist()})
        if labels is not None and changed == 0:
            break
        labels = new
        for j in range(k):
            if (labels == j).any():
                C[j] = X[labels == j].mean(0)
    return {"labels": labels.tolist(), "history": history}


def choose_k(X: np.ndarray, ks=range(4, 13), seed: int = 0, sample: int = 1500) -> dict:
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), min(sample, len(X)), replace=False)
    rows = []
    for k in ks:
        km = KMeans(n_clusters=k, n_init=4, random_state=seed).fit(X)
        sil = silhouette_score(X[idx], km.labels_[idx], metric="cosine")
        rows.append({"k": k, "silhouette": round(float(sil), 4), "sse": round(float(km.inertia_), 2)})
    best = max(rows, key=lambda r: r["silhouette"])
    return {"rows": rows, "best_k": best["k"]}


def label_clusters(labels, tfidf_X, terms, k_words: int = 3, exclude=None) -> dict[int, str]:
    stop = set(stopwords()) | set(exclude or ())
    out = {}
    labels = np.asarray(labels)
    for c in sorted(set(labels.tolist())):
        rows = np.flatnonzero(labels == c)
        centroid = np.asarray(tfidf_X[rows].mean(axis=0)).ravel()
        words = []
        for j in np.argsort(-centroid):
            t = terms[j]
            if "_" in t or t in stop or len(t) < 2 or not vietnamese_syllable(t):
                continue
            words.append(t)
            if len(words) >= k_words:
                break
        out[int(c)] = " · ".join(words)
    return out


def agglomerative_steps(X: np.ndarray, names: list[str], max_steps: int = 12) -> dict:
    """Gộp dần hai cụm gần nhất (average link, khoảng cách cosine) và ghi lại các bước."""
    n = len(X)
    clusters = {i: [i] for i in range(n)}
    S = X @ X.T
    D = 1 - S
    steps = []
    while len(clusters) > 1 and len(steps) < max_steps:
        best = None
        keys = list(clusters)
        for a_i in range(len(keys)):
            for b_i in range(a_i + 1, len(keys)):
                a, b = keys[a_i], keys[b_i]
                d = float(D[np.ix_(clusters[a], clusters[b])].mean())
                if best is None or d < best[0]:
                    best = (d, a, b)
        d, a, b = best
        steps.append({"step": len(steps) + 1, "dist": round(d, 4),
                      "a": [names[i] for i in clusters[a]], "b": [names[i] for i in clusters[b]]})
        clusters[a] = clusters[a] + clusters.pop(b)
    model = AgglomerativeClustering(n_clusters=min(4, n), metric="cosine", linkage="average").fit(X)
    return {"steps": steps, "labels": model.labels_.tolist()}


def dbscan_roles(X: np.ndarray, eps: float = 0.35, min_samples: int = 4) -> dict:
    db = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit(X)
    core = np.zeros(len(X), bool)
    core[db.core_sample_indices_] = True
    roles = np.where(core, "lõi", np.where(db.labels_ >= 0, "biên", "nhiễu"))
    return {"labels": db.labels_.tolist(), "roles": roles.tolist(),
            "n_clusters": int(len(set(db.labels_.tolist())) - (1 if -1 in db.labels_ else 0)),
            "n_noise": int((db.labels_ == -1).sum())}


def cluster_results(Z: np.ndarray, doc_rows: list[int], tfidf_X, terms, k: int | None = None,
                    seed: int = 0, exclude=None) -> dict:
    """Phân cụm các kết quả đầu của một truy vấn (SEG 6.3 Clustering the results)."""
    if len(doc_rows) < 6:
        return {}
    k = k or min(4, max(2, len(doc_rows) // 8))
    X = Z[doc_rows]
    km = KMeans(n_clusters=k, n_init=3, random_state=seed).fit(X)
    names = label_clusters(km.labels_, tfidf_X[doc_rows], terms, 2, exclude)
    return {int(i): f"Nhóm {int(c) + 1}: {names[int(c)]}" for i, c in zip(doc_rows, km.labels_)}
