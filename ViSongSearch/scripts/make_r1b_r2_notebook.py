"""Write kaggle/visongsearch_r1b_r2.ipynb: one Kaggle run (GPU T4 x2, Internet on) that applies the
files changed since the dataset visongsearch-bundle-r1 (v2), reruns round 1 and then runs round 2.

    python scripts/make_r1b_r2_notebook.py

The changed files travel inside the notebook as a base64 tar.gz, so no new dataset is needed.
work_full/pilot_doc_ids.json (548 song ids, no lyrics) is made from work_pilot/corpus.jsonl."""
import ast, base64, io, json, lzma, os, tarfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FILES = ["visong/dense.py", "visong/llmtools.py", "scripts/round1_analyses.py", "scripts/extra_analyses.py",
         "scripts/llm_r2.py"]


def minify(path):
    """Same code without comments and docstrings (smaller notebook); the repo keeps the originals."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree).encode()


def main():
    ids = sorted({json.loads(l)["doc_id"] for l in open(os.path.join(ROOT, "work_pilot", "corpus.jsonl"), encoding="utf-8")})
    buf = io.BytesIO()
    def add(t, name, data):
        ti = tarfile.TarInfo(name); ti.size = len(data); t.addfile(ti, io.BytesIO(data))
    with tarfile.open(fileobj=buf, mode="w") as t:
        for rel in FILES:
            add(t, f"files/{rel}", minify(os.path.join(ROOT, rel)))
        add(t, "files/work_full/pilot_doc_ids.json", json.dumps(ids).encode())
        add(t, "driver.py", open(os.path.join(ROOT, "kaggle", "r1b_r2_driver.py"), "rb").read())
    b64 = base64.b85encode(lzma.compress(buf.getvalue(), preset=9 | lzma.PRESET_EXTREME)).decode()
    src = ("# ViSongSearch: chạy lại vòng 1 (đã sửa) rồi vòng 2. GPU T4 x2, Internet = On, dataset visongsearch-bundle-r1.\n"
           "import base64, io, lzma, tarfile, os\n"
           f"B = '{b64}'\n"
           "os.makedirs('/kaggle/working/patch', exist_ok=True)\n"
           "tarfile.open(fileobj=io.BytesIO(lzma.decompress(base64.b85decode(B)))).extractall('/kaggle/working/patch')\n"
           "exec(open('/kaggle/working/patch/driver.py', encoding='utf-8').read())\n")
    nb = {"cells": [{"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": src}],
          "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                       "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
    out = os.path.join(ROOT, "kaggle", "visongsearch_r1b_r2.ipynb")
    json.dump(nb, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(ROOT, "kaggle", "r1b_r2_cell.py"), "w", encoding="utf-8").write(src)
    print(out, len(b64))


if __name__ == "__main__":
    main()
