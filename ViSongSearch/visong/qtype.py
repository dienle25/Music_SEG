"""Query-type classification (TMG 03 Naive Bayes, TMG 04 logistic regression).

Predicts whether a query is an exact fragment, typed without marks, noisy,
paraphrased or descriptive. The prediction picks fusion weights per type
(tuned on dev), so the search engine trusts the lexical lists more for exact
fragments and the dense list more for descriptions.
"""
from __future__ import annotations

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import MultinomialNB

from .text import clean, has_diacritics, remove_diacritics


class QueryFeatures:
    DENSE = ("marked", "oov_marked", "oov_plain", "length")

    def __init__(self, vocab_marked: set[str], vocab_plain: set[str], drop: tuple[str, ...] = ()):
        self.vocab_marked = vocab_marked
        self.vocab_plain = vocab_plain
        self.keep = [i for i, f in enumerate(self.DENSE) if f not in drop]      # for ablations
        self.tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 4), min_df=2, sublinear_tf=True)

    def dense_feats(self, queries: list[str]) -> np.ndarray:
        rows = []
        for q in queries:
            toks = clean(q).split() or [""]
            n = len(toks)
            marked = sum(has_diacritics(t) for t in toks) / n
            oov_marked = sum(t not in self.vocab_marked for t in toks) / n
            oov_plain = sum(remove_diacritics(t) not in self.vocab_plain for t in toks) / n
            rows.append([marked, oov_marked, oov_plain, min(n, 40) / 40.0])
        return np.array(rows)[:, self.keep]

    def fit_transform(self, queries):
        return sparse.hstack([self.tfidf.fit_transform(queries), sparse.csr_matrix(self.dense_feats(queries))]).tocsr()

    def transform(self, queries):
        return sparse.hstack([self.tfidf.transform(queries), sparse.csr_matrix(self.dense_feats(queries))]).tocsr()


class QueryTypeClassifier:
    """`balanced` corrects for class imbalance: the rule types have about a hundred times more
    training examples than Q4/Q5. Logistic regression then weights each class by the inverse of
    its frequency; Naive Bayes uses a uniform class prior. `drop` removes hand-made features."""

    def __init__(self, vocab_marked, vocab_plain, model: str = "logreg", balanced: bool = False,
                 drop: tuple[str, ...] = ()):
        self.features = QueryFeatures(vocab_marked, vocab_plain, drop)
        self.model_name = model
        self.balanced = balanced
        self.model = (LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced" if balanced else None)
                      if model == "logreg" else MultinomialNB(alpha=0.1, fit_prior=not balanced))

    def fit(self, queries: list[str], labels: list[str]):
        self.model.fit(self.features.fit_transform(queries), labels)
        return self

    def predict(self, queries: list[str]) -> list[str]:
        return list(self.model.predict(self.features.transform(queries)))


def cv_macro_f1(texts: list[str], labels: list[str], groups: list[str], vocab_marked, vocab_plain,
                model: str, balanced: bool, folds: int = 5, seed: int = 0) -> float:
    """Macro-F1 of a classifier setting under k-fold cross-validation on the training queries.
    Folds are grouped by song cluster, so the queries of one song never sit on both sides."""
    from sklearn.metrics import f1_score
    from sklearn.model_selection import StratifiedGroupKFold
    y = np.array(labels)
    scores = []
    for tr, te in StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed).split(texts, y, groups):
        clf = QueryTypeClassifier(vocab_marked, vocab_plain, model, balanced)
        clf.fit([texts[i] for i in tr], list(y[tr]))
        scores.append(f1_score(y[te], clf.predict([texts[i] for i in te]), average="macro"))
    return float(np.mean(scores))
