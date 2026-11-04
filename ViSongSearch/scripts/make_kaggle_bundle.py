"""Zip the code and one work folder for upload as a Kaggle dataset.

Run files, vectors and models are left out: the notebook rebuilds them.

    python3 scripts/make_kaggle_bundle.py work_pilot /mnt/project-files/visongsearch/kaggle/visongsearch-bundle.zip
"""
import hashlib
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = ["visong", "scripts", "tests", "app", "crawler", "kaggle"]
WORK_FILES = ["corpus.jsonl", "passages.jsonl", "clusters.json", "queries", "results"]


def data_version(work):
    """Hashes of the corpus and query files, so returned results can be matched to the data they used."""
    files = ["corpus.jsonl", "clusters.json"] + sorted(
        os.path.join("queries", f) for f in os.listdir(os.path.join(ROOT, work, "queries")) if f.endswith(".jsonl"))
    out = {}
    for f in files:
        path = os.path.join(ROOT, work, f)
        if os.path.exists(path):
            out[f] = hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
    out["all"] = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()[:12]
    return out


def main(work="work_pilot", out="visongsearch-bundle.zip"):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    version = data_version(work)
    json.dump(version, open(os.path.join(ROOT, work, "results", "data_version.json"), "w"), indent=1)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for top in CODE + [os.path.join(work, f) for f in WORK_FILES]:
            path = os.path.join(ROOT, top)
            if os.path.isfile(path):
                z.write(path, top)
                n += 1
                continue
            for dirpath, dirnames, files in os.walk(path):
                dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".pytest_cache")]
                for f in files:
                    if f.endswith((".pyc", ".sqlite")) or f.startswith("report_smoke"):
                        continue
                    full = os.path.join(dirpath, f)
                    z.write(full, os.path.relpath(full, ROOT))
                    n += 1
    print(f"{out}: {n} files, {os.path.getsize(out) / 1e6:.1f} MB, data version {version['all']}")


if __name__ == "__main__":
    main(*sys.argv[1:])
