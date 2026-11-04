"""Dense retrieval with bi-encoders (TMG 05 embeddings, TMG 07 Transformers, SEG 12 passages).

Every passage (4 lyric lines) gets one vector; a song's score is its best
passage score (MaxP, Dai & Callan 2019). Vectors are L2-normalised, so the dot
product is the cosine similarity; brute-force search with numpy is exact and
fast enough for a few hundred thousand passages.

    python -m visong.dense encode --work work --model models/multilingual-e5-base --name e5base
    python -m visong.dense search --work work --model models/multilingual-e5-base --name e5base
    python -m visong.dense search --work work --model models/multilingual-e5-base --name e5base --restored all
    python -m visong.dense encode ... --cache-dir ~/emb_cache   # encode only passages not seen before

With --restored, each query is searched in its diacritic-restored form (queries/restored.json,
written by the lexical stage), the pipeline counterpart of noise-aware fine-tuning.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time

import numpy as np

from .corpus import read_jsonl
from .queries import QUERY_FILES
from .text import clean, words

# Run-name suffix of a search with restored queries: dense_e5base_rest uses the restorer trained on
# every indexed song ("all", what a deployed engine would use); "train" gives dense_e5base_resttrain.
REST_SUFFIX = "_rest"


def rest_suffix(restored: str | None) -> str:
    if not restored:
        return ""
    return REST_SUFFIX if restored == "all" else f"{REST_SUFFIX}{restored}"


def query_texts(work: str, queries: list[dict], restored: str | None = None) -> list[str]:
    """The text to encode for each query: as typed, or restored by the diacritic restorer
    named `restored`. A query the restorer has not seen keeps its own text."""
    texts = [q["text"] for q in queries]
    if not restored:
        return texts
    rq = json.load(open(os.path.join(work, "queries", "restored.json"), encoding="utf-8"))[restored]
    return [rq.get(q["qid"], t) for q, t in zip(queries, texts)]


# How each model expects its inputs.
MODEL_CONFIG = {
    "e5": {"query": "query: ", "passage": "passage: ", "segment": False},
    "bge-m3": {"query": "", "passage": "", "segment": False},
    "Vietnamese_Embedding": {"query": "", "passage": "", "segment": False},
    "vietnamese-bi-encoder": {"query": "", "passage": "", "segment": True},   # PhoBERT: word-segmented input
}


def config_for(model_path: str) -> dict:
    name = os.path.basename(model_path.rstrip("/"))
    for key, cfg in MODEL_CONFIG.items():
        if key in name:
            return cfg
    return {"query": "", "passage": "", "segment": False}


def prepare(texts, prefix: str, segment: bool) -> list[str]:
    out = []
    for t in texts:
        t = clean(t)
        if segment:
            t = " ".join(words(t))
        out.append(prefix + t)
    return out


class DenseEncoder:
    def __init__(self, model_path: str, max_length: int = 128, batch_size: int = 64, device: str | None = None,
                 fp16: bool = True):
        import torch
        from sentence_transformers import SentenceTransformer
        torch.set_num_threads(max(1, os.cpu_count() or 1))
        device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = SentenceTransformer(model_path, device=device)
        if fp16 and device == "cuda":
            self.model.half()                      # inference only; vectors are cast back to float32
        self.model.max_seq_length = max_length
        self.cfg = config_for(model_path)
        self.batch_size = batch_size

    def encode_passages(self, texts) -> np.ndarray:
        return self._encode(prepare(texts, self.cfg["passage"], self.cfg["segment"]))

    def encode_queries(self, texts) -> np.ndarray:
        return self._encode(prepare(texts, self.cfg["query"], self.cfg["segment"]))

    def _encode(self, texts) -> np.ndarray:
        # Sorting by length keeps padding small, which matters a lot on CPU.
        order = np.argsort([len(t) for t in texts])
        vecs = self.model.encode([texts[i] for i in order], batch_size=self.batch_size,
                                 normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        out = np.empty_like(vecs)
        out[order] = vecs
        return out.astype(np.float32)


class DenseIndex:
    """Passage vectors grouped by song; song score = max passage score (MaxP)."""

    def __init__(self, vectors: np.ndarray, pids: list[str], doc_of: list[str]):
        order = np.argsort(np.array(doc_of), kind="stable")          # make each song's passages contiguous
        self.vectors = np.ascontiguousarray(vectors[order])
        self.pids = [pids[i] for i in order]
        docs_sorted = np.array(doc_of)[order]
        change = np.r_[True, docs_sorted[1:] != docs_sorted[:-1]]
        self.starts = np.flatnonzero(change)
        self.docs = docs_sorted[self.starts]

    def search_batch(self, qvecs: np.ndarray, k: int = 100, chunk: int = 128) -> list[list[tuple[str, float]]]:
        results = []
        kk = min(k, len(self.docs))
        for start in range(0, len(qvecs), chunk):
            sims = qvecs[start:start + chunk] @ self.vectors.T                     # (q, passages)
            best = np.maximum.reduceat(sims, self.starts, axis=1)                  # (q, songs)
            top = np.argpartition(-best, kk - 1, axis=1)[:, :kk]
            for i in range(best.shape[0]):
                idx = top[i][np.argsort(-best[i, top[i]])]
                results.append([(str(self.docs[j]), float(best[i, j])) for j in idx])
        return results

    def best_passages(self, qvec: np.ndarray, doc_ids: list[str], n: int = 2) -> dict[str, list[int]]:
        """Indices of the n most similar passages of each given song (used by the reranker)."""
        pos = {d: i for i, d in enumerate(self.docs)}
        ends = np.r_[self.starts[1:], len(self.vectors)]
        out = {}
        for d in doc_ids:
            i = pos.get(d)
            if i is None:
                continue
            a, b = self.starts[i], ends[i]
            sims = self.vectors[a:b] @ qvec
            out[d] = list(a + np.argsort(-sims)[:n])
        return out


def weights_fingerprint(model_path: str) -> str:
    """SHA-1 of the model's weight files: two models with the same name but other weights
    (an NA-FT model trained again) never share cached vectors."""
    h = hashlib.sha1()
    if not os.path.isdir(model_path):
        # A hub id (e.g. "intfloat/multilingual-e5-base"): fingerprint the local snapshot that
        # SentenceTransformer loads; without one, the id and its revision.
        try:
            from huggingface_hub import snapshot_download
            model_path = snapshot_download(model_path)
        except Exception:
            try:
                from huggingface_hub import model_info
                rev = model_info(model_path).sha
            except Exception:
                rev = "unknown"
            h.update(f"{model_path}@{rev}".encode())
            return h.hexdigest()
    for f in sorted(os.listdir(model_path)):
        if f.endswith((".safetensors", ".bin")) and not f.startswith("training_args"):
            with open(os.path.join(model_path, f), "rb") as fh:
                for block in iter(lambda: fh.read(1 << 24), b""):
                    h.update(block)
    return h.hexdigest()


def _load_cache(cache: str):
    """Keys and vectors of every complete shard (shard-N.npy written first, then shard-N.keys)."""
    keys, parts = [], []
    for f in sorted(os.listdir(cache)):
        if f.endswith(".keys"):
            ks = open(os.path.join(cache, f)).read().split()
            v = np.load(os.path.join(cache, f[:-5] + ".npy"))
            if len(ks) == len(v):
                keys += ks
                parts.append(v)
    return {k: i for i, k in enumerate(keys)}, (np.vstack(parts) if parts else None)


def _cached_vectors(enc_factory, texts, keys, cache, chunk: int = 4096):
    """Vectors of `texts`, encoding only the texts whose key is not in the cache directory yet.
    New vectors are saved every `chunk` texts, so an interrupted run loses at most one chunk."""
    os.makedirs(cache, exist_ok=True)
    known, cvecs = _load_cache(cache)
    first = {}
    for i, k in enumerate(keys):
        first.setdefault(k, i)
    new_keys = [k for k in first if k not in known]
    secs, parts = 0.0, [] if cvecs is None else [cvecs]
    if new_keys:
        enc = enc_factory()
        n_shard = sum(f.endswith(".keys") for f in os.listdir(cache))
        for a in range(0, len(new_keys), chunk):
            ks = new_keys[a:a + chunk]
            t0 = time.time()
            v = enc.encode_passages([texts[first[k]] for k in ks])
            secs += time.time() - t0
            name = os.path.join(cache, f"shard-{n_shard:05d}")
            np.save(name + ".tmp.npy", v)
            os.replace(name + ".tmp.npy", name + ".npy")
            with open(name + ".keys.tmp", "w") as fh:
                fh.write("\n".join(ks) + "\n")
            os.replace(name + ".keys.tmp", name + ".keys")
            n_shard += 1
            for k in ks:
                known[k] = len(known)
            parts.append(v)
    cvecs = np.vstack(parts)
    return cvecs[[known[k] for k in keys]], len(new_keys), secs


def encode_corpus(work: str, model_path: str, name: str, max_length: int = 128, batch_size: int = 64,
                  cache_root: str | None = None):
    """Encode every passage. With `cache_root`, vectors already computed by the same weights
    (<cache_root>/<name>-<fingerprint>) are reused, so a growing collection is encoded once."""
    passages = read_jsonl(os.path.join(work, "passages.jsonl"))
    texts = [x["text"] for x in passages]
    if cache_root:
        fp = weights_fingerprint(model_path)
        keys = [hashlib.sha1(f"{max_length}\n{t}".encode("utf-8")).hexdigest() for t in texts]
        vecs, encoded, secs = _cached_vectors(lambda: DenseEncoder(model_path, max_length, batch_size),
                                              texts, keys, os.path.join(cache_root, f"{name}-{fp[:12]}"))
    else:
        enc = DenseEncoder(model_path, max_length, batch_size)
        t0 = time.time()
        vecs = enc.encode_passages(texts)
        secs, encoded = time.time() - t0, len(texts)
    out = os.path.join(work, "dense", name)
    os.makedirs(out, exist_ok=True)
    np.save(os.path.join(out, "passages.npy"), vecs)
    json.dump({"pids": [x["pid"] for x in passages], "docs": [x["doc_id"] for x in passages],
               "model": model_path, "seconds": secs, "passages_encoded": encoded, "dim": int(vecs.shape[1])},
              open(os.path.join(out, "meta.json"), "w"))
    print(f"{name}: {len(passages)} passages, {encoded} encoded in {secs:.0f}s", flush=True)


def load_index(work: str, name: str) -> DenseIndex:
    d = os.path.join(work, "dense", name)
    meta = json.load(open(os.path.join(d, "meta.json")))
    return DenseIndex(np.load(os.path.join(d, "passages.npy")), meta["pids"], meta["docs"])


def search_queries(work: str, model_path: str, name: str, query_files=QUERY_FILES,
                   splits=("dev", "test"), k: int = 100, include_sweep: bool = True,
                   restored: str | None = None):
    """Search the index `name` with the dev and test queries and the noise sweep; the run is
    dense_{name}, or dense_{name}_rest when the queries are restored first (restored="all")."""
    from .evaluate import write_trec
    queries = []
    for f in query_files:
        path = os.path.join(work, "queries", f)
        if os.path.exists(path):
            queries += [q for q in read_jsonl(path) if q["split"] in splits]
    sweep_path = os.path.join(work, "queries", "noise_sweep.jsonl")
    if include_sweep and os.path.exists(sweep_path):
        queries += read_jsonl(sweep_path)
    texts = query_texts(work, queries, restored)
    enc = DenseEncoder(model_path)
    t0 = time.time()
    qv = enc.encode_queries(texts)
    t_enc = time.time() - t0
    index = load_index(work, name)
    t0 = time.time()
    res = index.search_batch(qv, k)
    t_search = time.time() - t0
    run = {q["qid"]: r for q, r in zip(queries, res)}
    suffix = rest_suffix(restored)
    os.makedirs(os.path.join(work, "runs"), exist_ok=True)
    write_trec(os.path.join(work, "runs", f"dense_{name}{suffix}.trec"), run, f"dense_{name}{suffix}")
    stats = {"queries": len(queries), "ms_per_query_encode": 1000 * t_enc / max(1, len(queries)),
             "ms_per_query_search": 1000 * t_search / max(1, len(queries)),
             "index_mb": index.vectors.nbytes / 1e6, "restored": restored,
             "queries_changed": sum(t != q["text"] for q, t in zip(queries, texts))}
    json.dump(stats, open(os.path.join(work, "dense", name, f"search_stats{suffix}.json"), "w"), indent=1)
    print(f"{name}{suffix}", stats, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["encode", "search", "both"])
    ap.add_argument("--work", default="work")
    ap.add_argument("--model", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--max-length", type=int, default=128)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--restored", choices=["all", "train"], help="search with diacritic-restored queries")
    ap.add_argument("--cache-dir", help="reuse passage vectors encoded before by the same weights")
    a = ap.parse_args(argv)
    if a.stage in ("encode", "both"):
        encode_corpus(a.work, a.model, a.name, a.max_length, a.batch_size, cache_root=a.cache_dir)
    if a.stage in ("search", "both"):
        search_queries(a.work, a.model, a.name, restored=a.restored)


if __name__ == "__main__":
    main()
