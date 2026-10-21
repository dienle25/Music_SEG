"""
Text Preprocessing and Search Engine Lab — BBC News dataset
Usage:
    python text_preprocessing_bbc_lab.py --data bbc-text.csv --out results
Dependencies:
    pip install pandas numpy scikit-learn nltk
This offline-friendly implementation uses regex tokenization, scikit-learn stop words,
Porter stemming, and a lightweight lemmatization fallback. See report.md for limitations.
"""
import argparse, re, math, collections, os
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer, ENGLISH_STOP_WORDS
from sklearn.metrics.pairwise import cosine_similarity
from nltk.stem import PorterStemmer

TOKEN_RE = re.compile(r"[a-z0-9]+(?:['’-][a-z0-9]+)*", re.I)
STOP_WORDS = set(ENGLISH_STOP_WORDS)
STEMMER = PorterStemmer()

def clean_text(text):
    text = str(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()

def tokenize(text):
    return TOKEN_RE.findall(clean_text(text))

def simple_lemma(word):
    # Offline fallback only; not equivalent to WordNet/POS-aware lemmatization.
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("ing"):
        x = word[:-3]
        if len(x) > 2 and x[-1] == x[-2]:
            x = x[:-1]
        return x
    if len(word) > 4 and word.endswith("ed"):
        x = word[:-2]
        if len(x) > 2 and x[-1] == x[-2]:
            x = x[:-1]
        return x
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word

def preprocess_tokens(text, strategy):
    tokens = tokenize(text)
    if strategy == "baseline":
        return tokens
    tokens = [t for t in tokens if t not in STOP_WORDS]
    if strategy == "no_stopwords":
        return tokens
    if strategy == "stemmed":
        return [STEMMER.stem(t) for t in tokens]
    if strategy == "lemmatized":
        return [simple_lemma(t) for t in tokens]
    raise ValueError("Unknown strategy: " + strategy)

def bm25_scores(corpus_tokens, query_tokens, k1=1.5, b=0.75):
    """Okapi BM25 score implementation, with standard Robertson-style IDF."""
    n_docs = len(corpus_tokens)
    lengths = np.array([len(doc) for doc in corpus_tokens], dtype=float)
    avgdl = lengths.mean() if n_docs else 0.0
    doc_freq = collections.Counter(term for doc in corpus_tokens for term in set(doc))
    scores = np.zeros(n_docs, dtype=float)
    query_freq = collections.Counter(query_tokens)
    for term, q_count in query_freq.items():
        n = doc_freq.get(term, 0)
        if not n:
            continue
        idf = math.log(1 + (n_docs - n + 0.5) / (n + 0.5))
        for i, doc in enumerate(corpus_tokens):
            freq = doc.count(term)
            if freq:
                norm = freq + k1 * (1 - b + b * (lengths[i] / avgdl if avgdl else 0))
                scores[i] += idf * (freq * (k1 + 1) / norm) * q_count
    return scores

def precision_at_k(ranked_ids, relevant_ids, k=5):
    return len(set(ranked_ids[:k]) & set(relevant_ids)) / k

def recall_at_k(ranked_ids, relevant_ids, k=5):
    if not relevant_ids:
        return None
    return len(set(ranked_ids[:k]) & set(relevant_ids)) / len(relevant_ids)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="bbc-text.csv", help="Path to bbc-text.csv")
    parser.add_argument("--out", default="results", help="Output folder")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    df = pd.read_csv(args.data)
    if "text" not in df.columns:
        raise ValueError("Dataset must contain a 'text' column.")
    if "category" not in df.columns:
        print("Warning: no category column; category fields will be omitted.")

    print("Original shape:", df.shape)
    print("Columns:", df.columns.tolist())
    print("Missing values:\\n", df.isnull().sum())
    print("Duplicate rows:", df.duplicated().sum())
    print("Duplicate article texts:", df["text"].duplicated().sum())
    if "category" in df.columns:
        print("Articles per category:\\n", df["category"].value_counts())

    duplicate_texts = df[df.duplicated(subset=["text"], keep=False)]
    conflict_count = 0
    if not duplicate_texts.empty and "category" in df.columns:
        conflict_count = int((duplicate_texts.groupby("text")["category"].nunique() > 1).sum())
    print("Duplicate texts with conflicting category labels:", conflict_count)

    df = df.dropna(subset=["text"]).copy()
    df["text"] = df["text"].astype(str)
    df = df.drop_duplicates(subset=["text"]).reset_index(drop=True)
    print("Documents after text deduplication:", len(df))

    df["clean_text"] = df["text"].apply(clean_text)
    strategies = ["baseline", "no_stopwords", "stemmed", "lemmatized"]
    models = {}
    vocab_rows = []
    for strategy in strategies:
        tokenized_docs = [preprocess_tokens(t, strategy) for t in df["text"]]
        processed_docs = [" ".join(tokens) for tokens in tokenized_docs]
        vectorizer = TfidfVectorizer(
            tokenizer=str.split, preprocessor=None, token_pattern=None,
            lowercase=False, ngram_range=(1, 1)
        )
        matrix = vectorizer.fit_transform(processed_docs)
        models[strategy] = {
            "tokens": tokenized_docs, "docs": processed_docs,
            "vectorizer": vectorizer, "matrix": matrix
        }
        vocab_rows.append({
            "Strategy": strategy,
            "Vocabulary size": len(vectorizer.vocabulary_),
            "Total tokens": sum(map(len, tokenized_docs))
        })

    def search(query, strategy, method, top_k=5):
        model = models[strategy]
        qtokens = preprocess_tokens(query, strategy)
        if method == "tfidf":
            query_vec = model["vectorizer"].transform([" ".join(qtokens)])
            scores = cosine_similarity(query_vec, model["matrix"]).ravel()
        elif method == "bm25":
            scores = bm25_scores(model["tokens"], qtokens)
        else:
            raise ValueError("method must be tfidf or bm25")
        top_indices = np.argsort(scores)[::-1][:top_k]
        rows = []
        for idx in top_indices:
            rows.append({
                "index": int(idx), "score": float(scores[idx]),
                "category": df.iloc[idx]["category"] if "category" in df.columns else None,
                "text": df.iloc[idx]["text"][:300]
            })
        return rows

    queries = [
        "digital video recorders",
        "WorldCom accounting fraud",
        "UK political election",
        "football championship"
    ]
    result_rows = []
    for query in queries:
        for strategy in strategies:
            for method in ["tfidf", "bm25"]:
                for rank, result in enumerate(search(query, strategy, method, 5), start=1):
                    result_rows.append({
                        "query": query, "strategy": strategy, "method": method,
                        "rank": rank, "doc_id": result["index"],
                        "score": round(result["score"], 6),
                        "category": result["category"],
                        "snippet": result["text"][:160]
                    })

    vocab_df = pd.DataFrame(vocab_rows)
    results_df = pd.DataFrame(result_rows)
    vocab_df.to_csv(os.path.join(args.out, "vocabulary_comparison.csv"), index=False)
    results_df.to_csv(os.path.join(args.out, "retrieval_top5_results.csv"), index=False)

    samples = []
    for idx in range(min(3, len(df))):
        text = df.iloc[idx]["text"]
        samples.append({
            "doc_id": idx,
            "category": df.iloc[idx]["category"] if "category" in df.columns else "",
            "raw": text[:350],
            "clean": clean_text(text)[:350],
            "first_30_tokens": " ".join(tokenize(text)[:30]),
            "token_count": len(tokenize(text))
        })
    pd.DataFrame(samples).to_csv(os.path.join(args.out, "cleaning_samples.csv"), index=False)

    before = sum(len(preprocess_tokens(t, "baseline")) for t in df["text"])
    after = sum(len(preprocess_tokens(t, "no_stopwords")) for t in df["text"])
    reduction = (before - after) / before * 100 if before else 0
    print("\\nToken count before stop-word removal:", before)
    print("Token count after stop-word removal:", after)
    print(f"Token reduction: {reduction:.2f}%")
    print("\\nVocabulary comparison:\\n", vocab_df.to_string(index=False))
    print("\\nResults saved to:", os.path.abspath(args.out))
    print("NOTE: Precision@K/Recall@K are not calculated until query-specific relevant document IDs are manually judged.")
    print("NOTE: lemmatized strategy uses an offline fallback; use WordNet + POS tagging for full lemmatization.")

if __name__ == "__main__":
    main()
