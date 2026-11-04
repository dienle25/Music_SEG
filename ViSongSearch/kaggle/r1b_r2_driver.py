"""Kaggle driver: round-1 rerun with fixes, then round 2 (written by scripts/make_r1b_r2_notebook.py).
The patch folder holds the files changed since the dataset visongsearch-bundle-r1 (v2) was made."""
import glob, json, os, shutil, subprocess, sys, time, zipfile

PATCH = "/kaggle/working/patch/files"
ROOT = "/kaggle/working/vss"
if not os.path.exists(ROOT):
    inits = glob.glob("/kaggle/input/**/visong/__init__.py", recursive=True)
    zips = glob.glob("/kaggle/input/**/visongsearch-bundle*.zip", recursive=True)
    if inits:
        shutil.copytree(os.path.dirname(os.path.dirname(inits[0])), ROOT)
    elif zips:
        zipfile.ZipFile(zips[0]).extractall(ROOT)
    else:
        raise SystemExit("Chưa thêm dataset visongsearch-bundle-r1 (Add Input).")
for dp, _, fns in os.walk(PATCH):
    for fn in fns:
        rel = os.path.relpath(os.path.join(dp, fn), PATCH)
        os.makedirs(os.path.dirname(os.path.join(ROOT, rel)), exist_ok=True)
        shutil.copy(os.path.join(dp, fn), os.path.join(ROOT, rel))
        print("patched", rel)
os.chdir(ROOT); os.makedirs("logs", exist_ok=True)

r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "sentence-transformers>=3.0", "datasets", "accelerate", "pyvi"],
                   capture_output=True, text=True)
print(r.stderr[-800:])
sys.path.insert(0, os.path.join(ROOT, "vendor"))
os.environ["PYTHONPATH"] = os.path.join(ROOT, "vendor") + os.pathsep + ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")
os.environ["TOKENIZERS_PARALLELISM"] = "false"; os.environ["PYTHONWARNINGS"] = "ignore"
import urllib.request
try:
    import pyvi  # noqa
    urllib.request.urlopen("https://huggingface.co", timeout=20)
except Exception as e:
    raise SystemExit(f"Không cài/vào mạng được ({e}): bật Session options → Internet = On rồi chạy lại")
try:
    from kaggle_secrets import UserSecretsClient
    os.environ["HF_TOKEN"] = UserSecretsClient().get_secret("HF_TOKEN")
except Exception:
    pass
import torch
print("GPU:", [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())])


def run(name, cmd):
    t0 = time.time(); print(f"\n===== {name}: {cmd}", flush=True)
    with open(f"logs/{name}.log", "w") as log:
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            log.write(line); log.flush()
            if "it/s]" not in line and "s/it]" not in line and not line.startswith(("Loading checkpoint", "Fetching", "Batches")):
                print(line, end="", flush=True)
        p.wait()
    st = {"returncode": p.returncode, "minutes": round((time.time() - t0) / 60, 1)}
    print(name, st, flush=True)
    return st


def zip_out(out, pats):
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for pat in pats:
            for path in glob.glob(pat, recursive=True):
                if os.path.isfile(path) and os.path.getsize(path) < 200e6:
                    z.write(path)
        for path in glob.glob("work_full/dense/*/meta.json"):
            meta = json.load(open(path))
            z.writestr(path, json.dumps({k: v for k, v in meta.items() if k not in ("pids", "docs")}))
    print(out, round(os.path.getsize(out) / 1e6, 1), "MB", flush=True)


status = {}
R1 = [("tests", "python -m pytest -q -p no:cacheprovider tests --ignore=tests/test_app.py"),
      ("experiments", "python scripts/run_gpu_experiments.py --work work_full --seeds 1,2,3 --max-examples 10000 --cache-dir /kaggle/working/emb_cache"),
      ("round1", "python -I scripts/round1_analyses.py work_full"),
      ("validity", "python scripts/validity_analyses.py work_full"),
      ("extra", "python -I scripts/extra_analyses.py work_full --perms 10000 --reps 5"),
      ("tmg", "python scripts/tmg_analyses.py work_full"),
      ("collection", "python scripts/collection_stats.py work_full"),
      ("tables", "python scripts/make_paper_tables.py --work work_full --out paper/generated")]
for name, cmd in R1:
    status[name] = run(name, cmd)
json.dump(status, open("logs/status.json", "w"), indent=1)
zip_out("/kaggle/working/visongsearch_results_r1b.zip",
        ["work_full/results/**/*", "paper/generated/*", "logs/*", "work_full/runs/*.qids",
         "work_full/models/*/naft_info.json", "work_full/dense/*/search_stats*.json"])
shutil.rmtree("/kaggle/working/emb_cache", ignore_errors=True)
shutil.rmtree("work_full/models", ignore_errors=True)       # free disk for the LLM downloads

# Round 2: several LLMs write Q4/Q5; LLM baselines (closed-book, RAG)
smoke = "/kaggle/tmp_smoke"
shutil.rmtree(smoke, ignore_errors=True); os.makedirs(smoke)
shutil.copytree("work_full/queries", f"{smoke}/work/queries")
for f in ("corpus.jsonl", "clusters.json"):
    shutil.copy(f"work_full/{f}", f"{smoke}/work/{f}")
status["r2_smoke"] = run("r2_smoke", f"python scripts/llm_r2.py run --work {smoke}/work --models Qwen/Qwen2.5-0.5B-Instruct "
                         f"--identify Qwen/Qwen2.5-0.5B-Instruct --test 6 --dev 2 --id-max 3 --cache /tmp/hf_smoke")
shutil.rmtree(smoke, ignore_errors=True)
status["r2"] = run("r2", "python scripts/llm_r2.py run --work work_full --deadline-min 420")
json.dump(status, open("logs/status_all.json", "w"), indent=1)
zip_out("/kaggle/working/visongsearch_results_r2.zip",
        ["work_full/queries/llm_multi*.jsonl", "work_full/results/llm_*", "work_full/runs/llm_*.trec", "logs/*"])
print(json.dumps(status, indent=1))
