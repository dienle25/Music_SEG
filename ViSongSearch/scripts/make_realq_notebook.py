"""Write kaggle/visongsearch_realq.ipynb with the current harvest_tot.py and resolve_tot.py inside.

The bundle already on Kaggle (visongsearch-bundle-r1) predates the two scripts, so the notebook
carries them as %%writefile cells and needs nothing but that bundle. Run this after changing either
script; tests/test_harvest_tot.py fails while the notebook holds an old copy.

    python scripts/make_realq_notebook.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "kaggle", "visongsearch_realq.ipynb")
WORKDIR = "/tmp/vss"        # code and collection: under /tmp, so lyrics and caches never become notebook output

INTRO = """# ViSongSearch: truy vấn thật "tìm tên bài hát" (chỉ cần CPU + Internet)

Notebook này thu các bài đăng **thật** của người đang cố nhớ tên một bài hát Việt, cùng câu trả lời mà người hỏi đã xác nhận, rồi gắn câu trả lời với cụm bài hát (cluster) trong `work_full`. Không có truy vấn nào được sinh ra: mỗi bản ghi là văn bản tải về từ nguồn, tên người dùng được thay bằng mã băm.

Nguồn: r/tipofmytongue qua bộ dữ liệu TOMT-KIS (Hugging Face, Apache-2.0) và Arctic Shift; các subreddit nhận diện bài hát và tiếng Việt; Reddit API (chỉ khi có khoá OAuth); chủ đề "tìm tên bài hát" trên diễn đàn XenForo (voz.vn), chỉ khi robots.txt cho phép. Mỗi nguồn chạy độc lập: nguồn nào bị chặn thì ghi log và chạy tiếp nguồn sau.

**Chuẩn bị (một lần):**
1. *Add Input* → chọn dataset `visongsearch-bundle-r1` (đã tải lên trước đó).
2. *Settings*: **Accelerator = None** (không tốn quota GPU), **Internet = On**.
3. (Tuỳ chọn) *Add-ons → Secrets*: `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` nếu nhóm có app Reddit đã đăng ký. Không có thì nguồn `reddit` thử các trang `.json` công khai; Reddit đã đóng chúng trong năm 2026 nên nhiều khả năng chỉ thấy dòng `blocked: HTTP 403` trong log, các nguồn khác không bị ảnh hưởng.
4. **Save Version → Save & Run All (Commit)**. Ước tính 30–90 phút (tải TOMT-KIS khoảng 2–3 GB; Arctic Shift tối đa 1 yêu cầu/giây; diễn đàn 1 trang/3 giây). Muốn thử nhanh trước: đặt `QUICK = True` ở bước 5.

**Khi xong:** tab *Output* → tải **visongsearch_realq.zip** → gửi lại vào thread của project. Zip chỉ có truy vấn đã ẩn danh, câu trả lời, kết quả gán cluster, file CSV để kiểm tra tay và log. Bộ đệm thô (còn tên người dùng) và lời bài hát nằm ở `/tmp`, không vào Output.
"""

FIND = f"""# 1. Find the bundle (Kaggle unzips uploaded datasets) and copy code + collection to {WORKDIR}
import glob, json, os, shutil, subprocess, sys, zipfile
ROOT, OUT = "{WORKDIR}", "/kaggle/working/realq"
if not os.path.exists(os.path.join(ROOT, "work_full", "clusters.json")):
    hits = [os.path.dirname(os.path.dirname(p)) for p in glob.glob("/kaggle/input/**/work_full/clusters.json", recursive=True)]
    hits = sorted((h for h in hits if os.path.exists(os.path.join(h, "visong", "__init__.py"))), key=lambda h: "r1" not in h)
    zips = sorted(glob.glob("/kaggle/input/**/visongsearch-bundle*.zip", recursive=True), key=lambda z: "r1" not in z)
    if hits:
        shutil.copytree(hits[0], ROOT, dirs_exist_ok=True, ignore=shutil.ignore_patterns(
            "passages.jsonl", "noise_sweep.jsonl", "rule.jsonl", "restored.json", "__pycache__"))
    elif zips:
        zipfile.ZipFile(zips[0]).extractall(ROOT)
    else:
        raise SystemExit("Add the visongsearch-bundle-r1 dataset to this notebook first (Add Input).")
os.makedirs(os.path.join(ROOT, "scripts"), exist_ok=True)
os.makedirs(OUT, exist_ok=True)
os.chdir(ROOT)
print("bundle copied to", ROOT, sorted(os.listdir(ROOT)))
version = "work_full/results/data_version.json"
print(open(version).read() if os.path.exists(version) else "(no data_version.json)")
"""

LIBS = """# 2. Libraries: requests, beautifulsoup4, pyarrow and huggingface_hub come with the Kaggle image;
#    datasketch (imported by visong.dedup) comes from the bundle's vendor folder, else from pip
import importlib.util, urllib.request
os.environ["PYTHONPATH"] = os.pathsep.join([ROOT, os.path.join(ROOT, "vendor"), os.environ.get("PYTHONPATH", "")])
sys.path[:0] = [ROOT, os.path.join(ROOT, "vendor")]
need = [pip for pip, mod in [("requests", "requests"), ("beautifulsoup4", "bs4"), ("pyarrow", "pyarrow"),
                             ("huggingface_hub", "huggingface_hub"), ("datasketch", "datasketch")]
        if importlib.util.find_spec(mod) is None]
if need:
    print("pip install", need)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *need], check=False)
try:
    urllib.request.urlopen("https://huggingface.co", timeout=20)
except Exception as e:
    raise SystemExit(f"No Internet ({e}): turn on Settings -> Internet and run again")
print(subprocess.run([sys.executable, "-c", "import datasketch, visong, requests, bs4, pyarrow, huggingface_hub; print('imports ok')"],
                     capture_output=True, text=True).stdout or "an import failed: see the cell above")
"""

SETTINGS = """# 5. Settings
SOURCES = "tomtkis,arctic,reddit,forum"
THREADS = [                       # XenForo 2 threads of song requests; robots.txt of each site decides
    "https://voz.vn/t/tong-hop-noi-nho-va-tim-ten-bai-hat.187681/",
    "https://voz.vn/t/xin-ten-bai-hat-a.1126142/",
]
QUICK = False                     # True: at most 30 posts per source and 3 pages per thread (about 10 minutes)
try:
    from kaggle_secrets import UserSecretsClient
    secrets_client = UserSecretsClient()
except Exception:
    secrets_client = None
for key in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD", "HF_TOKEN", "VSS_ANON_SALT"):
    try:
        os.environ[key] = secrets_client.get_secret(key)
    except Exception:
        pass                      # secret not set: that option is simply not used
print({k: bool(os.environ.get(k)) for k in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "HF_TOKEN", "VSS_ANON_SALT")})
"""

HARVEST = """# 6. Harvest. Raw responses and the TOMT-KIS download are cached in /tmp/realq_cache (they hold
#    usernames, so they stay out of the output); running the cell again reuses them.
import shlex
args = ["--sources", SOURCES, "--out", f"{OUT}/harvest.jsonl", "--cache", "/tmp/realq_cache"]
for t in THREADS:
    args += ["--thread", t]
if QUICK:
    args += ["--max-posts", "30", "--forum-max-pages", "3"]
HARVEST_ARGS = " ".join(shlex.quote(a) for a in args)
!python scripts/harvest_tot.py {HARVEST_ARGS}
"""

RESOLVE = """# 7. Map the answers to clusters of work_full (minutes)
!python scripts/resolve_tot.py work_full {OUT}/harvest.jsonl --out {OUT}/resolved.jsonl
"""

SUMMARY = """# 8. Summary
h = json.load(open(f"{OUT}/harvest_stats.json"))
print("harvest:", json.dumps({k: {x: v[x] for x in ("status", "scanned", "kept", "answered") if x in v} for k, v in h.items() if k != "total"}, ensure_ascii=False))
print("total:", json.dumps(h["total"], ensure_ascii=False))
print("resolve:", json.dumps(json.load(open(f"{OUT}/resolved_stats.json")), ensure_ascii=False, indent=1))
rows = [json.loads(l) for l in open(f"{OUT}/resolved.jsonl", encoding="utf-8")]
for r in [r for r in rows if r["cluster_id"] and not r["needs_manual_check"]][:5]:
    print(r["id"], "->", r["matched_title"], "|", r["matched_artist"], "| confidence", r["confidence"])
"""

PACKAGE = """# 9. Package the small files: records, manual-check CSV, stats and logs (no cache, no lyrics)
out = "/kaggle/working/visongsearch_realq.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for path in sorted(glob.glob(f"{OUT}/*")):
        if os.path.isfile(path):
            z.write(path, os.path.join("realq", os.path.basename(path)))
    if os.path.exists(version):
        z.write(version, "realq/data_version.json")
    names = z.namelist()
print(out, round(os.path.getsize(out) / 1e6, 2), "MB:", names)
"""

END = """Xong. Tải **visongsearch_realq.zip** ở tab *Output* và gửi vào thread của project.

Trong zip: `harvest.jsonl` (truy vấn thật + câu trả lời, đã ẩn danh), `resolved.jsonl` (thêm `cluster_id`, `confidence`, `needs_manual_check`, `check_reasons`), `resolved_check.csv` (mở bằng Google Sheets; với mỗi dòng cần kiểm tra, điền `verified_cluster_id` sau khi mở link bài đăng, hoặc ghi `none` nếu bài hát không có trong bộ sưu tập), các file `_stats.json` và log.
"""


def cell(kind, text, i):
    lines = text.splitlines(keepends=True)
    c = {"cell_type": kind, "id": f"cell-{i:02d}", "metadata": {}, "source": lines}
    if kind == "code":
        c.update({"execution_count": None, "outputs": []})
    return c


def writefile(name, i):
    with open(os.path.join(ROOT, "scripts", name), encoding="utf-8") as f:
        script = f.read()
    return cell("code", f"%%writefile {WORKDIR}/scripts/{name}\n{script}", i)


def build():
    parts = [("markdown", INTRO), ("code", FIND), ("code", LIBS),
             ("markdown", "3–4. The two scripts of the repository (`scripts/harvest_tot.py`, `scripts/resolve_tot.py`), "
                          "written into the copied bundle. Generated by `scripts/make_realq_notebook.py`: edit the "
                          "scripts there, not here.\n"),
             ("write", "harvest_tot.py"), ("write", "resolve_tot.py"), ("code", SETTINGS), ("code", HARVEST),
             ("code", RESOLVE), ("code", SUMMARY), ("code", PACKAGE), ("markdown", END)]
    cells = [writefile(text, i) if kind == "write" else cell(kind, text, i) for i, (kind, text) in enumerate(parts)]
    return {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                         "language_info": {"name": "python"},
                                         "kaggle": {"accelerator": "none", "isInternetEnabled": True}},
            "nbformat": 4, "nbformat_minor": 5}


def main():
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(build(), f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(OUT)


if __name__ == "__main__":
    main()
