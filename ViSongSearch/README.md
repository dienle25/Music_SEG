# ViSongSearch

Finding Vietnamese songs from imperfectly remembered lyrics. This repository holds a known-item test
collection for Vietnamese song search, a small search engine built on it, and every experiment of the
paper in `paper/`. It is also the group project of the SEG301 (Search Engines) and TMG301 (Text Mining)
courses.

A query is what someone types when they have forgotten a song's title: a lyric fragment, often without
diacritics, misheard, mistyped or paraphrased, or a description of the song. Every query has one correct
answer, the cluster of versions of one song.

## Contents

| Path | What it is |
|---|---|
| `crawler/hac_crawler.py` | Polite, resumable crawler for Hợp Âm Chuẩn (robots.txt, 1 request per second, SQLite state) |
| `visong/corpus.py` | Loading and cleaning lyrics, passages |
| `visong/dedup.py` | Near-duplicate song clusters (MinHash + LSH, title rule) |
| `visong/queries.py`, `visong/noise.py` | Query types Q1–Q5, Q4u/Q5u, noise channel, noise sweep, LLM query filters |
| `visong/text.py`, `visong/lexical.py` | Analyzers, inverted index, BM25, query likelihood, RM3, multi-field BM25 |
| `visong/diacritics.py` | Diacritic restoration (Kneser–Ney trigram LM, beam search, Telex decoding) |
| `visong/qtype.py` | Query-type classifier (Naive Bayes, logistic regression) |
| `visong/fusion.py` | Reciprocal rank fusion and weight tuning |
| `visong/dense.py`, `visong/finetune.py`, `visong/rerank.py` | Bi-encoders (MaxP), noise-aware fine-tuning, cross-encoder reranking |
| `visong/evaluate.py` | Metrics, paired randomization test, Holm correction, Kendall's tau |
| `visong/pipeline.py` | The stages: build, queries, lexical, fuse, report |
| `app/` | FastAPI web demo: result snippets, song pages with JSON-LD, sitemap, query and click log |
| `scripts/` | Collection statistics, text-mining analyses, paper tables, Kaggle bundle, LLM and human queries |
| `kaggle/` | Notebooks for the GPU experiments and for a CPU crawl on Kaggle |
| `paper/` | LaTeX manuscript; its numbers, tables and figure are generated from the results |
| `docs/human_queries/` | Human-query questionnaire (Google Apps Script) and its song list |
| `work_pilot/` | Pilot collection (548 songs): queries, run files and results |

## Install

Python 3.10 or newer.

    pip install -r requirements.txt          # collection, lexical experiments, demo, tests
    pip install -r requirements-gpu.txt      # dense retrieval, fine-tuning, reranking

    python -m pytest -q                      # 45 unit tests, about 10 seconds

## Rebuild the collection and run the experiments

1. **Crawl** (hours; stops after `--hours` and resumes where it stopped):

       python crawler/hac_crawler.py --test 10416 11844        # check the parser on two pages
       python crawler/hac_crawler.py --db data/hac.sqlite --max-songs 15000 --hours 1.9
       python crawler/hac_crawler.py --db data/hac.sqlite --export data/hac.jsonl

2. **Corpus, clusters, splits and rule-based queries** (Q1–Q3, noise sweep):

       python -m visong.pipeline build --work work --dbs <folder of the group's SQLite databases> --jsonl data/hac.jsonl
       python -m visong.pipeline queries --work work

3. **LLM queries** (Q4 paraphrases, Q5 descriptions) for a sample of development and test clusters:
   every pilot cluster plus the clusters whose key hashes below `queries.LLM_SAMPLE` (12% of test, 6% of
   development clusters; the sample does not depend on the crawl order).

       python scripts/prep_llm_items.py work --sample --skip-covered --out items.json   # clusters still to cover
       # write the queries with the prompts of visong/queries.py (PROMPT_Q4, PROMPT_Q5), then:
       python scripts/add_llm_queries.py work <queries file> <generator name> items.json
       python scripts/sync_llm_queries.py work                 # re-attach after a rebuild; prints the coverage

   Every query passes the automatic filters of `check_llm_query`; rejected ones are listed for rewriting.
   The unmarked variants Q4u/Q5u are written automatically. The queries of the paper are in
   `work_full/queries/llm.jsonl` (out-of-sample ones in `llm_extra.jsonl`).

4. **Lexical experiments and report** (CPU, minutes):

       python -m visong.pipeline lexical --work work
       python -m visong.pipeline report --work work            # work/results/report_test.md

5. **Neural experiments** (dense, NA-FT, hybrid fusion, reranking). Every bi-encoder, zero-shot and
   fine-tuned, is searched twice: with the queries as typed (`dense_<name>`) and after diacritic restoration
   (`dense_<name>_rest`, the restorer of step 4); the dev score decides which one enters fusion. The paper's
   runs were made on a 4-core CPU:

       python scripts/check_naft_overlap.py --snapshot <work folder NA-FT was trained on> --work work --drop
       python scripts/run_gpu_experiments.py --work work --models e5small=<path>,e5base=<path>,bkai=<path> \
           --naft-bases e5small=<path> --seeds 1,2,3 --max-examples 10000 --cache-dir emb_cache \
           --rerankers bgererank=<path> --rerank-queries 280
       python scripts/bench_efficiency.py --work work --models e5small=<path>,...   # timings on this machine

   `--cache-dir` keeps passage vectors per model, so a growing collection is encoded once. The overlap check
   is needed only when NA-FT was trained on an earlier snapshot of the collection. On a GPU, the same script
   runs every model of `MODELS` (BGE-M3 and Vietnamese_Embedding too) from a Kaggle bundle:

       python scripts/make_kaggle_bundle.py work visongsearch-bundle.zip

   Upload the zip as a Kaggle dataset, run `kaggle/visongsearch_gpu.ipynb` with a GPU (instructions in the
   notebook), and unzip the returned `visongsearch_results.zip` into `work/`. `results/data_version.json`
   records which corpus and queries the results belong to. Then run the report again.

6. **Human queries** (test only):

       python scripts/make_human_form.py work docs/human_queries
       # run docs/human_queries/create_form.gs in Google Apps Script, share the form, export the answers as CSV
       python scripts/ingest_human_queries.py work docs/human_queries/responses.csv

7. **Paper**:

       python scripts/crawl_stats.py data/hac.sqlite work      # pages fetched, songs, dates
       python scripts/collection_stats.py work
       python scripts/tmg_analyses.py work                     # n-gram order, per-class F1, edit distance, clustering
       python scripts/make_paper_tables.py --work work --out paper/generated
       cd paper && pdflatex main && bibtex main && pdflatex main && pdflatex main

8. **Web demo**:

       VISONG_WORK=work uvicorn app.main:app --port 8000     # http://localhost:8000, statistics at /stats

`python -m visong.pipeline all --work work --dbs ... --jsonl ...` runs steps 2 and 4 in one go.

## Data and copyright

Song lyrics belong to their authors and publishers. The released collection contains song identifiers,
source URLs, cluster assignments, queries and run files, not the lyrics; the crawler and the pipeline
rebuild the corpus from the source pages. `work_pilot/corpus.jsonl` and `work_pilot/passages.jsonl` hold
lyrics for local experiments only: remove them before publishing this repository. The demo shows at most
eight lines of a song and links to the source page.
