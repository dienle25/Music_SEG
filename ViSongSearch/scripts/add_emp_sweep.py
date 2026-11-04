"""Add the noise sweeps whose channels come from real transcription variation.

    python3 scripts/add_emp_sweep.py work_full

Reads WORK/results/real_noise.json (scripts/real_noise_stats.py) and, for the clean test
fragments of the sweep (type N0), appends the same noise rates corrupted with
- E10..E50 (qid suffix -e<rate>): the hand-made operations re-weighted by the observed shares;
- W10..W50 (qid suffix -w<rate>): the observed channel (visong.noise.EmpiricalChannel), whose
  substitution kinds, whole words included, follow the observed shares.
Earlier E and W entries are replaced.
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong import corpus as C                          # noqa: E402
from visong.noise import EmpiricalChannel, corrupt      # noqa: E402


def main(work, rates=(0.1, 0.2, 0.3, 0.4, 0.5), seed=11):
    real = json.load(open(os.path.join(work, "results", "real_noise.json")))["transcriptions"]
    weights = real["channel_shares"]
    channel = EmpiricalChannel(real["substitution_pairs"])
    path = os.path.join(work, "queries", "noise_sweep.jsonl")
    sweep = [q for q in C.read_jsonl(path) if q["type"][:1] not in ("E", "W")]
    rng, rng_w, extra = random.Random(seed), random.Random(seed + 1), []
    for q in sweep:
        if q["type"] != "N0":
            continue
        base = q["qid"][:-len("-r0")]
        for r in rates:
            text, info = corrupt(q["text"], r, rng, weights=weights)
            extra.append({**q, "qid": f"{base}-e{int(r * 100)}", "type": f"E{int(r * 100)}", "text": text,
                          "meta": {**q["meta"], **info, "channel": "empirical"}})
        for r in rates:
            text, info = channel.corrupt(q["text"], r, rng_w)
            extra.append({**q, "qid": f"{base}-w{int(r * 100)}", "type": f"W{int(r * 100)}", "text": text,
                          "meta": {**q["meta"], **info}})
    C.write_jsonl(path, sweep + extra)
    print(len(extra), "queries added; E weights", weights, "W kinds", dict(channel.kind_counts))


if __name__ == "__main__":
    main(sys.argv[1])
