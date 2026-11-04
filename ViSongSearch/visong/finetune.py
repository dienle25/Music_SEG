"""NA-FT: noise-aware fine-tuning of a bi-encoder (TMG 08 fine-tuning).

Training pairs come only from TRAINING songs and need no LLM:
1. rule queries (exact, no-diacritic, misheard/mistyped) -> the passage they were cut from;
2. noisy Inverse Cloze Task (Lee et al., 2019): one lyric line, sometimes stripped of
   marks or corrupted by the noise channel -> the rest of its passage.
Each pair gets one hard negative: a passage from ANOTHER song cluster that BM25
(diacritic-free bigrams) ranks high for the query. Loss: multiple negatives
ranking loss (in-batch negatives + the hard negative).

    python -m visong.finetune --work work --base models/multilingual-e5-small --seed 1 --out naft_e5small_s1
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time

from .corpus import read_jsonl
from .dense import config_for, prepare
from .lexical import LexicalIndex
from .noise import corrupt
from .text import clean, remove_diacritics


def build_examples(work: str, seed: int = 1, ict_per_song: int = 6, max_examples: int | None = None,
                   noise: bool = True, hard_negatives: bool = True):
    """Training triplets. noise=False keeps only clean pairs (exact fragments and clean
    ICT), i.e. plain domain adaptation; hard_negatives=False draws the negative at random
    from another song cluster. Both switches exist for the ablation study."""
    rng = random.Random(seed)
    songs = {s["doc_id"]: s for s in read_jsonl(os.path.join(work, "corpus.jsonl"))}
    train_docs = {d for d, s in songs.items() if s["extra"].get("split") == "train"}
    cluster = {d: s["extra"]["cluster"] for d, s in songs.items()}
    passages = [p for p in read_jsonl(os.path.join(work, "passages.jsonl")) if p["doc_id"] in train_docs]
    by_doc = {}
    for p in passages:
        by_doc.setdefault(p["doc_id"], []).append(p)

    examples = []
    # 1. rule queries -> passage containing the fragment
    for q in read_jsonl(os.path.join(work, "queries", "rule.jsonl")):
        if q["split"] != "train" or q["doc_id"] not in by_doc:
            continue
        frag = remove_diacritics(q["meta"]["source_fragment"])
        cands = [p for p in by_doc[q["doc_id"]] if frag in remove_diacritics(clean(p["text"]))]
        if not cands:
            line = q["meta"].get("line", 0)
            cands = [p for p in by_doc[q["doc_id"]] if p["line_start"] <= line < p["line_start"] + 4]
        if cands:
            # without noise the same pair is kept but with the clean fragment as the query
            text = q["text"] if noise else q["meta"]["source_fragment"]
            examples.append((text, rng.choice(cands)["text"], q["doc_id"], q["type"]))
    # 2. noisy inverse cloze task
    for d, ps in by_doc.items():
        for _ in range(ict_per_song):
            p = rng.choice(ps)
            lines = p["text"].split("\n")
            if len(lines) < 3:
                continue
            i = rng.randrange(len(lines))
            query = clean(lines[i])
            if len(query.split()) < 4:
                continue
            r = rng.random()
            if noise and r < 0.3:
                query = remove_diacritics(query)
            elif noise and r < 0.5:
                query = corrupt(query, rng.choice([0.1, 0.2, 0.3]), rng)[0]
            context = "\n".join(lines[:i] + lines[i + 1:])
            examples.append((query, context, d, "ICT"))
    rng.shuffle(examples)
    if max_examples:
        examples = examples[:max_examples]

    # hard negatives from passage-level BM25 over training passages
    index = LexicalIndex([p["text"] for p in passages], [p["pid"] for p in passages], "nodiac12", 0.9, 0.4)
    text_of = {p["pid"]: p["text"] for p in passages}
    doc_of = {p["pid"]: p["doc_id"] for p in passages}
    triplets = []
    for query, pos, d, kind in examples:
        negs = []
        if hard_negatives:
            negs = [pid for pid, _ in index.search(query, k=30) if cluster[doc_of[pid]] != cluster[d]]
        while not negs:
            cand = rng.choice(passages)["pid"]
            if cluster[doc_of[cand]] != cluster[d]:
                negs = [cand]
        triplets.append({"anchor": query, "positive": pos, "negative": text_of[rng.choice(negs[:10])], "kind": kind})
    return triplets


def train(work: str, base: str, out: str, seed: int = 1, epochs: int = 1, batch_size: int = 32,
          lr: float = 3e-5, max_examples: int | None = None, max_length: int = 128,
          noise: bool = True, hard_negatives: bool = True):
    import torch
    from datasets import Dataset
    from sentence_transformers import SentenceTransformer, SentenceTransformerTrainer, losses
    from sentence_transformers.training_args import BatchSamplers, SentenceTransformerTrainingArguments

    random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(max(1, os.cpu_count() or 1))
    t0 = time.time()
    trip = build_examples(work, seed, max_examples=max_examples, noise=noise, hard_negatives=hard_negatives)
    cfg = config_for(base)
    data = {
        "anchor": prepare([t["anchor"] for t in trip], cfg["query"], cfg["segment"]),
        "positive": prepare([t["positive"] for t in trip], cfg["passage"], cfg["segment"]),
        "negative": prepare([t["negative"] for t in trip], cfg["passage"], cfg["segment"]),
    }
    kinds = {}
    for t in trip:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    print(f"examples {len(trip)} {kinds} built in {time.time() - t0:.0f}s", flush=True)
    model = SentenceTransformer(base, device="cuda" if torch.cuda.is_available() else "cpu")
    model.max_seq_length = max_length
    out_dir = os.path.join(work, "models", out)
    import transformers
    # transformers 5 takes the warmup share as a float warmup_steps; 4.x uses warmup_ratio
    warmup = ({"warmup_steps": 0.1} if int(transformers.__version__.split(".")[0]) >= 5
              else {"warmup_ratio": 0.1})
    args = SentenceTransformerTrainingArguments(
        output_dir=out_dir + "_ckpt", num_train_epochs=epochs, per_device_train_batch_size=batch_size,
        learning_rate=lr, seed=seed, save_strategy="no", logging_steps=50, **warmup,
        batch_sampler=BatchSamplers.NO_DUPLICATES, report_to=[], fp16=torch.cuda.is_available(),
        dataloader_num_workers=0, disable_tqdm=True)
    trainer = SentenceTransformerTrainer(model=model, args=args, train_dataset=Dataset.from_dict(data),
                                         loss=losses.MultipleNegativesRankingLoss(model))
    t1 = time.time()
    trainer.train()
    model.save(out_dir)
    # keep the base model's name in the folder so dense.config_for picks the same prefixes
    json.dump({"base": base, "seed": seed, "examples": len(trip), "kinds": kinds, "epochs": epochs,
               "batch_size": batch_size, "lr": lr, "noise": noise, "hard_negatives": hard_negatives,
               "train_seconds": time.time() - t1},
              open(os.path.join(out_dir, "naft_info.json"), "w"), indent=1)
    print(f"saved {out_dir} after {time.time() - t1:.0f}s", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="work")
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True, help="folder name under work/models; keep 'e5' in it for e5 bases")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--max-examples", type=int)
    ap.add_argument("--no-noise", action="store_true", help="ablation: clean pairs only")
    ap.add_argument("--random-negatives", action="store_true", help="ablation: no BM25 hard negatives")
    a = ap.parse_args(argv)
    train(a.work, a.base, a.out, a.seed, a.epochs, a.batch_size, a.lr, a.max_examples,
          noise=not a.no_noise, hard_negatives=not a.random_negatives)


if __name__ == "__main__":
    main()
