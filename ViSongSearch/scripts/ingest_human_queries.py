"""Turn the Google Form responses (CSV export) into test queries of type H.

    python3 scripts/ingest_human_queries.py work_pilot docs/human_queries/responses.csv

Each non-empty answer becomes one query whose target is the song cluster of the
question (#Sxx in the column header). Answers that name the song's title are kept
apart (meta.excluded = "title_leak"): the study is about searching without the title.
"""
import csv
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong.corpus import load_corpus, write_jsonl        # noqa: E402
from visong.text import clean, remove_diacritics          # noqa: E402

NON_ANSWERS = re.compile(r"(?i)^\s*(không biết|ko biết|k biết|khong biet|ko bt|kbt|không nhớ|ko nhớ|khong nho|"
                         r"chưa nghe|chua nghe|không|khong|ko|k|no|\?+|-+|\.+|x)\s*$")


def norm(text: str) -> str:
    return remove_diacritics(clean(text))


def main(work, csv_path, prompts_path=None):
    prompts_path = prompts_path or os.path.join(os.path.dirname(csv_path), "human_prompts.json")
    prompts = {p["pid"]: p for p in json.load(open(prompts_path))}
    songs = {s.doc_id: s for s in load_corpus(os.path.join(work, "corpus.jsonl"))}
    cluster_split = {s.extra["cluster"]: s.extra["split"] for s in songs.values()}
    lyrics_of_cluster = {}
    for s in songs.values():
        lyrics_of_cluster.setdefault(s.extra["cluster"], set()).update(norm(s.lyrics).split())
    rows = list(csv.reader(open(csv_path, encoding="utf-8-sig")))
    header, data = rows[0], rows[1:]
    col_pid = {i: m.group(1) for i, h in enumerate(header) if (m := re.search(r"#(S\d+)\s*$", h.strip()))}
    habit_col = next((i for i, h in enumerate(header) if "thường gõ" in h), None)
    device_col = next((i for i, h in enumerate(header) if "làm khảo sát trên" in h), None)
    out, stats = [], Counter()
    for r, row in enumerate(data, 1):
        for i, pid in col_pid.items():
            text = row[i].strip() if i < len(row) else ""
            if not text:
                continue
            stats["answers"] += 1
            p = prompts[pid]
            toks = norm(text).split()
            meta = {"respondent": r, "prompt": pid, "habit": row[habit_col] if habit_col is not None else "",
                    "device": row[device_col] if device_col is not None else "", "length": len(toks)}
            title = norm(p["title"])
            if NON_ANSWERS.match(text) or len(toks) < 2:
                stats["non_answer"] += 1
                continue
            if len(title.split()) >= 2 and re.search(rf"\b{re.escape(title)}\b", " ".join(toks)):
                meta["excluded"] = "title_leak"
                stats["title_leak"] += 1
            lyr = lyrics_of_cluster.get(p["cluster"], set())
            meta["lyric_overlap"] = round(sum(t in lyr for t in toks) / len(toks), 3)
            meta["has_diacritics"] = norm(text) != clean(text)
            # cluster ids can change after a rebuild; the representative doc fixes the target
            doc = songs.get(p["doc_id"])
            cluster = doc.extra["cluster"] if doc else p["cluster"]
            out.append({"qid": f"H{r:03d}-{pid}", "type": "H", "text": text,
                        "doc_id": p["doc_id"], "cluster": cluster, "split": cluster_split.get(cluster, "test"),
                        "meta": meta})
    kept = [q for q in out if "excluded" not in q["meta"]]
    write_jsonl(os.path.join(work, "queries", "human.jsonl"), kept)
    write_jsonl(os.path.join(work, "queries", "human_excluded.jsonl"), [q for q in out if "excluded" in q["meta"]])
    stats["kept"] = len(kept)
    stats["respondents"] = len(data)
    stats["songs_covered"] = len({q["meta"]["prompt"] for q in kept})
    stats["lyric_like (overlap>=0.6)"] = sum(q["meta"]["lyric_overlap"] >= 0.6 for q in kept)
    stats["without_diacritics"] = sum(not q["meta"]["has_diacritics"] for q in kept)
    print(json.dumps(stats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
