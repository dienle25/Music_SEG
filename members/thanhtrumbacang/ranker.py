
from pathlib import Path
import sqlite3, pickle, re, unicodedata
import numpy as np
from scipy.sparse import load_npz
from sklearn.preprocessing import normalize
from sklearn.feature_extraction.text import TfidfVectorizer

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "music_ranked.db"
X = load_npz(ROOT / "data" / "tfidf_matrix.npz")
with open(ROOT / "data" / "tfidf_vectorizer.pkl","rb") as f:
    V = pickle.load(f)
with open(ROOT / "data" / "bm25_stats.pkl","rb") as f:
    B = pickle.load(f)
with open(ROOT / "data" / "bm25_docs.pkl","rb") as f:
    DOCS = pickle.load(f)

def normalize_text(s):
    s = unicodedata.normalize("NFC", (s or "").lower())
    s = re.sub(r"\s+", " ", s).strip()
    return s

def bm25_scores(query):
    terms = normalize_text(query).split()
    scores = np.zeros(B["N"], dtype=float)
    k1, b, avgdl = B["k1"], B["b"], B["avgdl"]
    # Query term frequency is capped by using unique terms; standard practical search behavior.
    for term in set(terms):
        if term not in B["idf"]:
            continue
        idf = B["idf"][term]
        for i, doc in enumerate(DOCS):
            tf = doc.count(term)
            if not tf:
                continue
            dl = B["doc_lengths"][i]
            scores[i] += idf * (tf*(k1+1)) / (tf + k1*(1-b+b*dl/avgdl))
    return scores

def minmax_or_max(scores):
    scores=np.asarray(scores,float)
    if not np.any(scores>0): return scores
    mx=scores.max()
    return scores/mx if mx>0 else scores

def search(query, top_k=10, tfidf_weight=0.5, bm25_weight=0.5):
    q = normalize_text(query)
    if not q:
        return []
    qv = V.transform([q])
    tfidf = (X @ qv.T).toarray().ravel()  # cosine because X and q are L2 normalized
    bm25 = bm25_scores(q)
    t=tfidf_weight*minmax_or_max(tfidf)
    b=bm25_weight*minmax_or_max(bm25)
    hybrid=t+b
    order=np.argsort(-hybrid)[:top_k]

    con=sqlite3.connect(DB)
    rows=[]
    for idx in order:
        row=con.execute("""
            SELECT id,title,artist,genre,url FROM songs_ranked WHERE id=?
        """,(int(idx)+1,)).fetchone()
        if row:
            rows.append({
                "id":row[0],"title":row[1],"artist":row[2],"genre":row[3],
                "url":row[4],"tfidf_score":round(float(tfidf[idx]),6),
                "bm25_score":round(float(bm25[idx]),6),
                "score":round(float(hybrid[idx]),6)
            })
    con.close()
    return rows

if __name__ == "__main__":
    import argparse, json
    p=argparse.ArgumentParser()
    p.add_argument("query")
    p.add_argument("--top-k",type=int,default=10)
    p.add_argument("--tfidf-weight",type=float,default=0.5)
    p.add_argument("--bm25-weight",type=float,default=0.5)
    a=p.parse_args()
    print(json.dumps(search(a.query,a.top_k,a.tfidf_weight,a.bm25_weight),
                     ensure_ascii=False,indent=2))
