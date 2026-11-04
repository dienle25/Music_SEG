"""Round 2 on a Kaggle GPU: several open LLMs write Q4/Q5 queries, and LLM baselines identify songs.

    python scripts/llm_r2.py run --work work_full [--models qwen25-7b,qwen3-8b,...]
                                 [--identify qwen25-7b,qwen3-8b] [--deadline-min 540]
    python scripts/llm_r2.py run --work work_full --backend fake      # CPU smoke test

For every model in --models, one after the other (each 7-8B model is loaded in float16 over both
T4 GPUs, then deleted with its download):
  1. generate: Q4 paraphrases and Q5 descriptions for every item of <work>/llm_prep/items_r2.json
     (built here on first use: the clusters of the existing LLM queries, then a uniform sample of
     more clusters, see visong.llmtools.build_items), with the paper's prompts, sampled at
     temperature 0.7 / top-p 0.9; an output that fails the paper's filter (check_llm_query) or the
     prompt's length limit is regenerated with another seed, at most --attempts times. Queries go
     to queries/llm_multi.jsonl (+ llm_multi_unmarked.jsonl), statistics to results/llm_multi_stats.json.
  2. identify (models in --identify only), on the test queries of the clusters that have the
     existing Q4/Q5, for all seven types:
     closed: the LLM names up to five songs (title - artist), matched to clusters by title;
     rag:    the LLM picks up to five of the top-20 clusters of a lexical first stage
             (equal-weight RRF of restored-syllable-bigram, unmarked-bigram, char-3 and metadata
             BM25); the picks go first, the rest of the first-stage list follows.
     Results go to results/llm_identify.json and runs/llm_{closed,rag}_<model>.trec.
Everything is saved after each model, and a rerun skips the models already done.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import random
import shutil
import sys
import time
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong import corpus as C                                                    # noqa: E402
from visong import llmtools as L                                                  # noqa: E402
from visong.diacritics import DiacriticRestorer                                   # noqa: E402
from visong.evaluate import bootstrap_ci, holm, per_query, randomization_test, write_trec  # noqa: E402
from visong.fusion import rrf                                                     # noqa: E402
from visong.lexical import LexicalIndex                                           # noqa: E402
from visong.queries import longest_common_run, unmarked_variants                  # noqa: E402
from visong.text import clean, has_diacritics, remove_diacritics                  # noqa: E402

MODELS = {
    "qwen25-7b": "Qwen/Qwen2.5-7B-Instruct",
    "qwen3-8b": "Qwen/Qwen3-8B",
    "seallm3-7b": "SeaLLMs/SeaLLMs-v3-7B-Chat",
    "sealion3-8b": "aisingapore/Llama-SEA-LION-v3-8B-IT",
    "vylinh-3b": "arcee-ai/Arcee-VyLinh",
    "llama31-8b": "meta-llama/Llama-3.1-8B-Instruct",          # gated: needs HF_TOKEN
}
DEFAULT_MODELS = "qwen25-7b,qwen3-8b,seallm3-7b,sealion3-8b,vylinh-3b"
ID_TYPES = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q4u", "Q5u")


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def path(work, *parts):
    p = os.path.join(work, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def load_json(p, default):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default


def save_json(p, obj):
    tmp = p + ".tmp"
    json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, p)


# ------------------------------------------------------------------ backends

class HFChat:
    """A chat model from the Hugging Face hub in float16 over all visible GPUs."""

    def __init__(self, repo: str, token: str | None = None, cache_dir: str | None = None):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch = torch
        self.repo, self.cache_dir = repo, cache_dir
        self.tok = AutoTokenizer.from_pretrained(repo, token=token, cache_dir=cache_dir)
        self.tok.padding_side = "left"
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        kw = dict(token=token, cache_dir=cache_dir, device_map="auto")
        try:
            self.model = AutoModelForCausalLM.from_pretrained(repo, dtype=torch.float16, **kw)
        except TypeError:                       # transformers < 4.56 names it torch_dtype
            self.model = AutoModelForCausalLM.from_pretrained(repo, torch_dtype=torch.float16, **kw)
        self.model.eval()
        self.revision = getattr(self.model.config, "_commit_hash", None)

    def _template(self, prompt: str) -> str:
        msgs = [{"role": "user", "content": prompt}]
        try:        # Qwen3: no thinking block; other templates ignore the flag
            return self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        except Exception:
            return self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)

    def chat(self, prompts: list[str], temperature: float, top_p: float, max_new_tokens: int, seed: int,
             batch_size: int) -> list[str]:
        torch = self.torch
        texts = [self._template(p) for p in prompts]
        order = sorted(range(len(texts)), key=lambda i: -len(texts[i]))
        out: list[str] = [""] * len(texts)
        start, bs = 0, batch_size
        while start < len(order):
            idx = order[start:start + bs]
            enc = self.tok([texts[i] for i in idx], return_tensors="pt", padding=True,
                           add_special_tokens=False).to(self.model.device)
            torch.manual_seed(seed + start)
            kw = dict(do_sample=True, temperature=temperature, top_p=top_p) if temperature > 0 else dict(do_sample=False)
            # float16 on a T4 can overflow in some models: invalid logits are replaced rather than
            # stopping the run, and a batch that still fails is retried greedily, then left empty
            # (an empty output is rejected and counted as "empty" in the statistics).
            kw.update(remove_invalid_values=True, renormalize_logits=True)
            gen, oom = None, False
            for attempt in range(2):
                try:
                    with torch.no_grad():
                        gen = self.model.generate(**enc, max_new_tokens=max_new_tokens,
                                                  pad_token_id=self.tok.pad_token_id, **kw)
                    break
                except torch.cuda.OutOfMemoryError:
                    oom = True
                    break
                except RuntimeError as e:
                    self.failed_batches = getattr(self, "failed_batches", 0) + 1
                    log(f"  generation error ({'greedy retry' if attempt == 0 else 'batch left empty'}): {e!r}"[:300])
                    kw = dict(do_sample=False, remove_invalid_values=True)
            if oom:
                del enc
                gc.collect()
                torch.cuda.empty_cache()
                if bs == 1:
                    raise RuntimeError("out of memory with batch size 1")
                bs = max(1, bs // 2)
                log(f"  out of memory: batch size {bs}")
                continue
            if gen is None:
                start += len(idx)
                continue
            new = gen[:, enc["input_ids"].shape[1]:]
            for j, i in enumerate(idx):
                out[i] = self.tok.decode(new[j], skip_special_tokens=True)
            start += len(idx)
        return out

    def close(self):
        del self.model
        gc.collect()
        self.torch.cuda.empty_cache()
        if self.cache_dir and os.path.isdir(self.cache_dir):
            shutil.rmtree(self.cache_dir, ignore_errors=True)


class FakeChat:
    """CPU stand-in for tests: paraphrases by dropping words, guesses the first candidate."""
    revision = "fake"

    def __init__(self, repo, *a, **k):
        self.repo = repo

    def chat(self, prompts, temperature, top_p, max_new_tokens, seed, batch_size):
        rng = random.Random(seed)
        out = []
        for p in prompts:
            if "ứng viên" in p:
                out.append("2, 1")
            elif "liệt kê tối đa 5 bài" in p:
                out.append("1. Mưa - Không rõ\n2. Em")
            elif "Đây là đoạn lời thật" in p:
                frag = p.split('"')[1]
                toks = clean(frag).split()
                keep = [t for t in toks if rng.random() < 0.6] + ["nhớ", "mang", "máng", "bài", "này"]
                out.append(" ".join(keep[:18]))
            else:
                out.append("bài hát buồn về mưa và người yêu cũ ở thành phố xa xôi nghe hồi xưa")
        return out

    def close(self):
        pass


def backend(kind: str, repo: str, token: str | None, cache_dir: str | None):
    return FakeChat(repo) if kind == "fake" else HFChat(repo, token, cache_dir)


# ------------------------------------------------------------------ data

def load_work(work):
    songs = C.load_corpus(os.path.join(work, "corpus.jsonl"))
    cluster_of = json.load(open(os.path.join(work, "clusters.json")))
    return songs, cluster_of


def read(work, name):
    p = os.path.join(work, "queries", name)
    return C.read_jsonl(p) if os.path.exists(p) else []


def get_items(work, songs, cluster_of, n_test, n_dev):
    p = path(work, "llm_prep", "items_r2.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    items = L.build_items(songs, cluster_of, read(work, "llm.jsonl"), n_test, n_dev)
    save_json(p, items)
    log(f"items {len(items)} {Counter((i['split'], i['origin']) for i in items)}")
    return items


# ------------------------------------------------------------------ 1. generation

def text_stats(rows, songs_by_id):
    """Surface statistics of the generated queries (TMG: how the generators differ)."""
    if not rows:
        return {}
    lens = [len(r["text"].split()) for r in rows]
    toks = [t for r in rows for t in r["text"].split()]
    big = [tuple(r["text"].split()[i:i + 2]) for r in rows for i in range(len(r["text"].split()) - 1)]
    runs = []
    for r in rows:
        lyr = remove_diacritics(clean(" ".join(songs_by_id[r["doc_id"]].lines()))).split()
        runs.append(longest_common_run(remove_diacritics(r["text"]).split(), lyr))
    return {"n": len(rows), "mean_syllables": float(np.mean(lens)), "median_syllables": float(np.median(lens)),
            "distinct1": len(set(toks)) / max(1, len(toks)), "distinct2": len(set(big)) / max(1, len(big)),
            "mean_longest_lyric_run": float(np.mean(runs)),
            "marked_share": float(np.mean([has_diacritics(r["text"]) for r in rows]))}


def generate(be, tag, items, by_id, a):
    rows, rejects, st = [], [], {}
    for qtype in ("Q4", "Q5"):
        pending = list(range(len(items)))
        reasons, final, first_try = Counter(), Counter(), 0
        for attempt in range(a.attempts):
            if not pending:
                break
            prompts = [L.prompt_for(qtype, items[i]) for i in pending]
            outs = be.chat(prompts, a.temperature, a.top_p, a.max_new_tokens, a.seed + 1000 * attempt + (qtype == "Q5"),
                           a.batch)
            nxt = []
            for i, raw in zip(pending, outs):
                it = items[i]
                text, ok, why = L.filter_output(qtype, raw, by_id[it["doc_id"]], it["frag"])
                reasons[why] += 1
                if ok:
                    first_try += attempt == 0
                    rows.append({"qid": f"{it['doc_id']}-{qtype}-{tag}", "type": qtype, "text": text,
                                 "doc_id": it["doc_id"], "cluster": it["cluster"], "split": it["split"],
                                 "meta": {"source_fragment": it["frag"] if qtype == "Q4" else None, "generator": tag,
                                          "model": be.repo, "revision": be.revision, "attempt": attempt,
                                          "origin": it["origin"], "length": len(text.split())}})
                else:
                    nxt.append(i)
                    if attempt == a.attempts - 1:
                        final[why] += 1
                    rejects.append({"generator": tag, "type": qtype, "doc_id": it["doc_id"], "attempt": attempt,
                                    "reason": why, "text": text[:300]})
            pending = nxt
        st[qtype] = {"items": len(items), "accepted": len(items) - len(pending), "accepted_first_try": first_try,
                     "outputs_by_reason": dict(reasons), "dropped_by_last_reason": dict(final)}
        log(f"  {tag} {qtype}: {st[qtype]['accepted']}/{len(items)} accepted ({first_try} first try) {dict(reasons)}")
    for qtype in ("Q4", "Q5"):
        st[qtype]["text"] = text_stats([r for r in rows if r["type"] == qtype], by_id)
    return rows, rejects, st


# ------------------------------------------------------------------ 2. identification

def id_queries(work, max_per_type: int | None, seed: int = 0):
    """Test queries of the clusters that have the existing Q4/Q5, all seven types."""
    llm = [q for q in read(work, "llm.jsonl") if q["split"] == "test"]
    clusters = sorted({q["cluster"] for q in llm})
    if max_per_type and len(clusters) > max_per_type:
        clusters = sorted(random.Random(seed).sample(clusters, max_per_type))
    keep = set(clusters)
    qs = [q for q in read(work, "rule.jsonl") + llm + read(work, "llm_unmarked.jsonl")
          if q["split"] == "test" and q["cluster"] in keep and q["type"] in ID_TYPES]
    return qs


def tune(index, dev, cluster_of, grid_k1=(0.3, 0.6, 0.9, 1.2, 1.5), grid_b=(0.3, 0.5, 0.75, 0.9)):
    def m(run):
        pq = per_query(run, dev, cluster_of)
        return float(np.mean([v["rr10"] for v in pq.values()])) if pq else 0.0
    best = (-1.0, (0.9, 0.4))
    for k1 in grid_k1:
        for b in grid_b:
            index.set_params(k1, b)
            s = m({q["qid"]: index.search(q["text"]) for q in dev})
            if s > best[0]:
                best = (s, (k1, b))
    index.set_params(*best[1])
    return {"k1": best[1][0], "b": best[1][1], "dev_mrr10": best[0]}


def first_stage(work, songs, cluster_of, queries, seed=0):
    """Equal-weight RRF of four lexical lists (the lists of the paper's lexical fusion),
    BM25 k1/b tuned on dev queries."""
    t0 = time.time()
    rng = random.Random(seed)
    dev = [q for q in read(work, "rule.jsonl") if q["split"] == "dev"]
    dev = rng.sample(dev, min(900, len(dev))) + [q for q in read(work, "llm.jsonl") + read(work, "llm_unmarked.jsonl")
                                                 if q["split"] == "dev"]
    texts, ids = [s.lyrics for s in songs], [s.doc_id for s in songs]
    meta = [" ".join(x for x in (s.title, s.artist, s.extra.get("singers", ""), s.genre) if x) for s in songs]
    rest = DiacriticRestorer(texts)
    restored = {q["qid"]: rest.restore(q["text"]) for q in queries + dev}
    params, lists = {}, defaultdict(dict)
    specs = [("restoreall_bm25_syl12", texts, "syl12", True), ("bm25_nodiac12", texts, "nodiac12", False),
             ("bm25_char3", texts, "char3", False), ("bm25_meta", meta, "nodiac12", False)]
    for name, docs, an, use_rest in specs:
        idx = LexicalIndex(docs, ids, an)
        dq = [{**q, "text": restored[q["qid"]]} for q in dev] if use_rest else dev
        params[name] = tune(idx, dq, cluster_of)
        for q in queries:
            lists[name][q["qid"]] = idx.search(restored[q["qid"]] if use_rest else q["text"], k=100)
        log(f"  first stage {name} {params[name]}")
        del idx
        gc.collect()
    run = {q["qid"]: rrf([lists[n][q["qid"]] for n, *_ in specs]) for q in queries}
    params["seconds"] = round(time.time() - t0, 1)
    return run, params


def summarize_types(pq: dict, queries, key="rr10"):
    out = {}
    for t in ID_TYPES:
        qids = [q["qid"] for q in queries if q["type"] == t and q["qid"] in pq]
        if qids:
            vals = np.array([pq[x][key] for x in qids], dtype=float)
            lo, hi = bootstrap_ci(vals)
            out[t] = {"n": len(qids), "mrr10": float(vals.mean()), "ci95": [lo, hi]}
    return out


def identify_closed(be, tag, queries, matcher, a):
    prompts = [L.PROMPT_CLOSED.format(query=q["text"]) for q in queries]
    outs = be.chat(prompts, 0.0, 1.0, 160, a.seed, a.batch_id)
    pq, trec = {}, {}
    rng = random.Random(a.seed)
    for q, raw in zip(queries, outs):
        guesses = L.parse_guesses(raw)
        blocks = matcher.blocks(guesses)
        rr, erank = L.expected_rr(blocks, q["cluster"])
        top5 = set(c for b in blocks[:5] for c in b)
        pq[q["qid"]] = {"rr10": rr, "expected_rank": erank, "n_guesses": len(guesses),
                        "n_matched": sum(len(b) for b in blocks), "lenient_hit5": float(q["cluster"] in top5),
                        "raw": raw[:300]}
        order = []
        for b in blocks:
            b = list(b)
            rng.shuffle(b)
            order += b
        trec[q["qid"]] = [(c, float(len(order) - i)) for i, c in enumerate(order)]
    return pq, trec


def identify_rag(be, tag, queries, first, by_id, cluster_of, a):
    cands = {q["qid"]: L.top_clusters(first[q["qid"]], cluster_of, a.rag_k) for q in queries}
    prompts = [L.PROMPT_RAG.format(query=q["text"], candidates=L.candidate_block(cands[q["qid"]], by_id, q["text"]))
               for q in queries]
    outs = be.chat(prompts, 0.0, 1.0, 40, a.seed, a.batch_rag)
    run, extra = {}, {}
    for q, raw in zip(queries, outs):
        c = cands[q["qid"]]
        chosen = L.parse_choice(raw, len(c))
        run[q["qid"]] = L.rerank(first[q["qid"]], c, chosen)
        extra[q["qid"]] = {"chosen": chosen, "gold_in_candidates": float(q["cluster"] in {x for x, _ in c}),
                           "raw": raw[:120]}
    pq = per_query(run, queries, cluster_of)
    for k, v in extra.items():
        pq[k].update(v)
    return pq, run


def significance(res, queries):
    """Paired randomization tests per type: each LLM run vs the first stage (RAG) and the RAG run
    vs the closed-book run of the same model; Holm over all tests of the file."""
    p, diffs = {}, {}
    fs = res["per_query"].get("first_stage", {})
    for mode in ("rag", "closed"):
        for tag, pq in res["per_query"].get(mode, {}).items():
            for t in ID_TYPES:
                qids = [q["qid"] for q in queries if q["type"] == t and q["qid"] in pq and q["qid"] in fs]
                if len(qids) < 5:
                    continue
                a_ = np.array([pq[x]["rr10"] for x in qids])
                b_ = np.array([fs[x]["rr10"] for x in qids])
                key = f"{mode}_{tag}_vs_first_stage_{t}"
                p[key] = randomization_test(a_, b_)
                diffs[key] = float(a_.mean() - b_.mean())
    adj = holm(p) if p else {}
    return {k: {"diff": diffs[k], "p": p[k], "p_holm": adj.get(k)} for k in p}


# ------------------------------------------------------------------ main

def run(a):
    t_start = time.time()
    deadline = t_start + 60 * a.deadline_min
    work = a.work
    songs, cluster_of = load_work(work)
    by_id = {s.doc_id: s for s in songs}
    items = get_items(work, songs, cluster_of, a.test, a.dev)
    token = os.environ.get("HF_TOKEN") or None
    tags = [t for t in a.models.split(",") if t]
    id_tags = set(t for t in (a.identify or "").split(",") if t)
    out_q = path(work, "queries", "llm_multi.jsonl")
    out_rej = path(work, "results", "llm_multi_rejects.jsonl")
    stats_p = path(work, "results", "llm_multi_stats.json")
    id_p = path(work, "results", "llm_identify.json")
    stats = load_json(stats_p, {"params": {}, "models": {}})
    stats["params"] = {"temperature": a.temperature, "top_p": a.top_p, "max_new_tokens": a.max_new_tokens,
                       "attempts": a.attempts, "seed": a.seed, "length_limits": L.LENGTH, "dtype": "float16",
                       "items": dict(Counter(f"{i['split']}/{i['origin']}" for i in items))}
    rows = read(work, "llm_multi.jsonl")
    res = load_json(id_p, {"params": {}, "first_stage": {}, "closed": {}, "rag": {}, "per_query": {}})

    queries, first, matcher = [], None, None
    if id_tags:
        queries = id_queries(work, a.id_max)
        log(f"identification queries {Counter(q['type'] for q in queries)}")
        first, fs_params = first_stage(work, songs, cluster_of, queries)
        pq = per_query(first, queries, cluster_of)
        res["first_stage"] = summarize_types(pq, queries)
        res["per_query"]["first_stage"] = {k: {"rr10": v["rr10"]} for k, v in pq.items()}
        res["params"] = {"first_stage": fs_params, "rag_k": a.rag_k, "decoding": "greedy",
                         "closed_max_new_tokens": 160, "rag_max_new_tokens": 40}
        write_trec(path(work, "runs", "llm_first_stage.trec"), first, "llm_first_stage")
        matcher = L.TitleMatcher(songs, cluster_of)
        log(f"first stage {res['first_stage']}")
        save_json(id_p, res)

    for tag in tags:
        need_gen = not stats["models"].get(tag, {}).get("done")
        need_id = tag in id_tags and not all(tag in res.get(m, {}) for m in ("closed", "rag"))
        if not (need_gen or need_id):
            log(f"{tag}: already done")
            continue
        if time.time() > deadline:
            log(f"deadline reached before {tag}")
            break
        repo = MODELS.get(tag, tag)
        t0 = time.time()
        try:
            be = backend(a.backend, repo, token, os.path.join(a.cache, tag))
        except Exception as e:                      # gated, missing, or does not fit
            log(f"{tag}: could not load {repo}: {e!r}"[:600])
            stats["models"][tag] = {"model": repo, "error": repr(e)[:600]}
            save_json(stats_p, stats)
            continue
        load_s = round(time.time() - t0, 1)
        try:
            if need_gen:
                t1 = time.time()
                new, rejects, st = generate(be, tag, items, by_id, a)
                rows = [r for r in rows if r["meta"].get("generator") != tag] + new
                C.write_jsonl(out_q, rows)
                C.write_jsonl(path(work, "queries", "llm_multi_unmarked.jsonl"), unmarked_variants(rows))
                with open(out_rej, "a", encoding="utf-8") as f:
                    for r in rejects:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
                stats["models"][tag] = {"model": repo, "revision": be.revision, "load_seconds": load_s,
                                        "failed_batches": getattr(be, "failed_batches", 0),
                                        "generate_seconds": round(time.time() - t1, 1), **st, "done": True}
                save_json(stats_p, stats)
            if need_id and time.time() < deadline:
                t1 = time.time()
                pq, trec = identify_closed(be, tag, queries, matcher, a)
                res["closed"][tag] = summarize_types(pq, queries)
                for t, v in res["closed"][tag].items():
                    v["lenient_hit5"] = float(np.mean([pq[q["qid"]]["lenient_hit5"] for q in queries if q["type"] == t]))
                res["per_query"].setdefault("closed", {})[tag] = pq
                write_trec(path(work, "runs", f"llm_closed_{tag}.trec"),
                           {k: [(d, s) for d, s in v] for k, v in trec.items()}, f"llm_closed_{tag}")
                log(f"  {tag} closed {round(time.time() - t1)}s {res['closed'][tag]}")
                save_json(id_p, res)
                t1 = time.time()
                pq, rrun = identify_rag(be, tag, queries, first, by_id, cluster_of, a)
                res["rag"][tag] = summarize_types(pq, queries)
                for t, v in res["rag"][tag].items():
                    v["first_stage_recall_k"] = float(np.mean([pq[q["qid"]]["gold_in_candidates"]
                                                               for q in queries if q["type"] == t]))
                res["per_query"].setdefault("rag", {})[tag] = pq
                write_trec(path(work, "runs", f"llm_rag_{tag}.trec"), rrun, f"llm_rag_{tag}")
                log(f"  {tag} rag {round(time.time() - t1)}s {res['rag'][tag]}")
                res.setdefault("models", {})[tag] = {"model": repo, "revision": be.revision}
                save_json(id_p, res)
        except Exception as e:
            log(f"{tag}: failed: {e!r}"[:600])
            stats["models"].setdefault(tag, {})["error"] = repr(e)[:600]
            save_json(stats_p, stats)
        finally:
            be.close()
            del be
            gc.collect()
    if queries:
        res["significance"] = significance(res, queries)
        save_json(id_p, res)
    stats["total_minutes"] = round((time.time() - t_start) / 60, 1)
    save_json(stats_p, stats)
    log(f"done in {stats['total_minutes']} min; {len(rows)} queries; "
        f"{Counter((r['meta']['generator'], r['type']) for r in rows)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "items"])
    ap.add_argument("--work", default="work_full")
    ap.add_argument("--models", default=DEFAULT_MODELS)
    ap.add_argument("--identify", default="qwen25-7b,qwen3-8b,seallm3-7b", help="models that also run the LLM baselines")
    ap.add_argument("--backend", default="hf", choices=["hf", "fake"])
    ap.add_argument("--cache", default="/tmp/hf_r2", help="download folder, deleted after each model")
    ap.add_argument("--test", type=int, default=600, help="test items for generation")
    ap.add_argument("--dev", type=int, default=150, help="dev items for generation")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--top-p", type=float, default=0.9)
    ap.add_argument("--max-new-tokens", type=int, default=96)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--batch-id", type=int, default=32)
    ap.add_argument("--batch-rag", type=int, default=8)
    ap.add_argument("--rag-k", type=int, default=20)
    ap.add_argument("--id-max", type=int, default=None, help="at most this many clusters for identification")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--deadline-min", type=float, default=540)
    a = ap.parse_args(argv)
    if a.cmd == "items":
        songs, cluster_of = load_work(a.work)
        get_items(a.work, songs, cluster_of, a.test, a.dev)
        return
    run(a)


if __name__ == "__main__":
    main()
