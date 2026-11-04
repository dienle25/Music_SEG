"""Write kaggle/visongsearch_r2_llm.ipynb: the round-2 LLM run on a Kaggle GPU (scripts/llm_r2.py).

    python scripts/make_r2_notebook.py

The Kaggle dataset `visongsearch-bundle-r1` holds the round-1 code and work_full/. The files that
round 2 adds are embedded in the notebook and written into the working copy, so no new dataset
version is needed; regenerate the notebook after changing any of them.
"""
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
EMBED = ["visong/llmtools.py", "scripts/llm_r2.py", "tests/test_llmtools.py"]


def code(src):
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": src}


def md(src):
    return {"cell_type": "markdown", "metadata": {}, "source": src}


INTRO = """# ViSongSearch – vòng 2: nhiều LLM viết truy vấn + LLM đoán tên bài (GPU)

1. *Add Input* → dataset **visongsearch-bundle-r1** (bản 2, có thư mục `vendor`).
2. *Session options*: **Accelerator = GPU T4 x2**, **Internet = On**.
3. (Không bắt buộc) *Add-ons → Secrets*: thêm `HF_TOKEN` nếu có tài khoản Hugging Face, để chạy thêm Llama-3.1.
4. **Save Version → Save & Run All (Commit)**. Ước tính 2,5–4 giờ. Mỗi mô hình lỗi thì bỏ qua và chạy mô hình sau.
5. Xong: tab *Output* → tải **visongsearch_results_r2.zip** → gửi lại cho nhóm.

Mô hình viết truy vấn: Qwen2.5-7B-Instruct, Qwen3-8B, SeaLLMs-v3-7B-Chat, SEA-LION-v3-8B-IT, Arcee-VyLinh (3B).
Mô hình đoán tên bài (closed-book và RAG): Qwen2.5-7B, Qwen3-8B, SeaLLMs-v3-7B."""

LOCATE = """import glob, os, shutil, zipfile, subprocess, sys, time, json
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
os.chdir(ROOT); os.makedirs("logs", exist_ok=True)
print(sorted(os.listdir(ROOT)))
print(open("work_full/results/data_version.json").read())"""

INSTALL = """r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "accelerate", "pyvi"], capture_output=True, text=True)
print(r.stdout[-300:], r.stderr[-800:])
sys.path.insert(0, os.path.join(ROOT, "vendor")); sys.path.insert(0, ROOT)
os.environ["PYTHONPATH"] = os.path.join(ROOT, "vendor") + os.pathsep + ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")
os.environ["TOKENIZERS_PARALLELISM"] = "false"; os.environ["PYTHONWARNINGS"] = "ignore"
import datasketch, torch, transformers, accelerate
import urllib.request
try:
    urllib.request.urlopen("https://huggingface.co", timeout=20)
except Exception as e:
    raise SystemExit(f"Không vào được huggingface.co ({e}): bật Session options → Internet = On rồi chạy lại")
try:
    from kaggle_secrets import UserSecretsClient
    os.environ["HF_TOKEN"] = UserSecretsClient().get_secret("HF_TOKEN")
    print("HF_TOKEN: có")
except Exception:
    print("HF_TOKEN: không có (bỏ qua các mô hình cần đăng nhập)")
print("torch", torch.__version__, "| transformers", transformers.__version__)
print("GPU:", [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())] or "KHÔNG CÓ GPU - bật Accelerator")"""

RUN = '''def run(name, cmd):
    """Stream a command's output into the notebook and logs/<name>.log."""
    t0 = time.time(); print(f"\\n===== {name}: {cmd}", flush=True)
    with open(f"logs/{name}.log", "w") as log:
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            log.write(line); log.flush()
            if not line.startswith(("Loading checkpoint", "Fetching")) and "it/s]" not in line:
                print(line, end="", flush=True)
        p.wait()
    st = {"returncode": p.returncode, "minutes": round((time.time() - t0) / 60, 1)}
    print(name, st, flush=True)
    return st

status = {}
status["tests"] = run("tests", "python -m pytest -q -p no:cacheprovider tests/test_llmtools.py")
# 1-2 minutes with a 0.5B model on a scratch copy: checks the whole GPU path before the long run.
shutil.rmtree("/kaggle/tmp_smoke", ignore_errors=True); os.makedirs("/kaggle/tmp_smoke", exist_ok=True)
shutil.copytree("work_full", "/kaggle/tmp_smoke/work")
status["smoke"] = run("smoke", "python scripts/llm_r2.py run --work /kaggle/tmp_smoke/work --models Qwen/Qwen2.5-0.5B-Instruct "
                      "--identify Qwen/Qwen2.5-0.5B-Instruct --test 6 --dev 2 --id-max 3 --cache /tmp/hf_smoke")
for l in open("/kaggle/tmp_smoke/work/queries/llm_multi.jsonl", encoding="utf-8").readlines()[:6] if os.path.exists("/kaggle/tmp_smoke/work/queries/llm_multi.jsonl") else []:
    print(json.loads(l)["type"], json.loads(l)["text"])
shutil.rmtree("/kaggle/tmp_smoke", ignore_errors=True)'''

MAIN = '''status["r2"] = run("r2", "python scripts/llm_r2.py run --work work_full --deadline-min 600")
json.dump(status, open("logs/status_r2.json", "w"), indent=1)
print(status)'''

ZIP = '''out = "/kaggle/working/visongsearch_results_r2.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for pat in ["work_full/queries/llm_multi*.jsonl", "work_full/results/llm_*", "work_full/runs/llm_*.trec", "logs/*"]:
        for path in glob.glob(pat):
            if os.path.isfile(path):
                z.write(path)
print(out, round(os.path.getsize(out) / 1e6, 1), "MB")
print(json.dumps(json.load(open("work_full/results/llm_multi_stats.json")), ensure_ascii=False)[:3000]
      if os.path.exists("work_full/results/llm_multi_stats.json") else "no stats")'''


def main():
    files = {rel: open(os.path.join(ROOT, rel), encoding="utf-8").read() for rel in EMBED}
    write = ("# Files round 2 adds to the round-1 bundle (written by scripts/make_r2_notebook.py)\n"
             f"FILES = {json.dumps(files, ensure_ascii=False, indent=0)}\n"
             "for rel, text in FILES.items():\n"
             "    os.makedirs(os.path.dirname(os.path.join(ROOT, rel)), exist_ok=True)\n"
             "    open(os.path.join(ROOT, rel), 'w', encoding='utf-8').write(text)\n"
             "print('wrote', list(FILES))")
    nb = {"cells": [md(INTRO), code(LOCATE), code(write), code(INSTALL), code(RUN), code(MAIN), code(ZIP)],
          "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                       "language_info": {"name": "python"},
                       "kaggle": {"accelerator": "nvidiaTeslaT4", "isInternetEnabled": True, "isGpuEnabled": True}},
          "nbformat": 4, "nbformat_minor": 5}
    out = os.path.join(ROOT, "kaggle", "visongsearch_r2_llm.ipynb")
    json.dump(nb, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(out, os.path.getsize(out))


if __name__ == "__main__":
    main()
