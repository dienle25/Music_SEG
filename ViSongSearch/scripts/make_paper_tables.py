"""Tables, numbers and figures of the paper, computed from the result files of one work folder.

    python3 scripts/make_paper_tables.py --work work_pilot --out paper/generated

Per-query ranks come from the run files in WORK/runs (lexical runs made here) and from
WORK/results/perquery_ranks.json.gz (runs made on the GPU machine). Every number in the
paper text that depends on the data is written as a LaTeX macro to numbers.tex, so the
paper follows the data when the experiments are re-run. Systems that have not been run
yet appear as "pending" rows; nothing is filled in by hand.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong.queries import QUERY_FILES                     # noqa: E402
from visong.corpus import read_jsonl                      # noqa: E402
from visong.dense import REST_SUFFIX                       # noqa: E402
from visong.evaluate import holm, per_query, randomization_test, read_trec   # noqa: E402

TYPES = ["Q1", "Q2", "Q3", "Q4", "Q5"]
UTYPES = ["Q4u", "Q5u"]            # Q4 and Q5 typed without marks
VARIANTS = ["Q3u", "Q4s", "Q5g"]   # test-only: Q3 without marks, Q4 by another prompt, Q5 from a gist
PARENT = {"Q4u": "Q4", "Q5u": "Q5", "Q3u": "Q3", "Q4s": "Q4", "Q5g": "Q5"}
WEIGHT_TAG = {0: "Zero", 0.05: "PtZeroFive", 0.1: "PtOne", 0.25: "Quarter", 0.5: "Half", 1: "One", 2: "Two",
              4: "Four", 8: "Eight", 16: "Sixteen"}
SWEEP = ["N0", "N10", "N20", "N30", "N40", "N50"]
METRICS = ("rr10", "s1", "s10", "r100")

LEXICAL_GROUPS = [
    ("Diacritic-sensitive", ["bm25_syl", "bm25_syl12", "bm25_word", "ql_syl", "rm3_syl", "rm3_syl12"]),
    ("Diacritic-insensitive", ["bm25_nodiac", "bm25_nodiac12", "rm3_nodiac", "bm25_char3", "bm25_char4x",
                               "bm25_char5x"]),
    ("Sound-alike key", ["bm25_phon12"]),
    ("Diacritic restoration", ["restoretrain_bm25_syl12", "restoreall_bm25_syl", "restoreall_bm25_syl12"]),
    ("Metadata", ["bm25_meta", "bm25_lyrmeta_nodiac12"]),
    ("Lexical fusion (RRF)", ["lexall_equal", "lexall_global", "lexall_adaptive", "lexall_oracle"]),
]
LABELS = {
    "bm25_syl": "BM25, syllables",
    "bm25_syl12": "BM25, syllable uni+bigrams",
    "bm25_word": "BM25, words (pyvi)",
    "ql_syl": "QL Dirichlet, syllables",
    "rm3_syl": "BM25+RM3, syllables",
    "rm3_syl12": "BM25+RM3, syllable uni+bigrams",
    "bm25_nodiac": "BM25, unmarked syllables",
    "bm25_nodiac12": "BM25, unmarked uni+bigrams",
    "rm3_nodiac": "BM25+RM3, unmarked",
    "bm25_char3": "BM25, char.\\ 3-grams in syllables",
    "bm25_char4x": "BM25, char.\\ 4-grams across syllables",
    "bm25_char5x": "BM25, char.\\ 5-grams across syllables",
    "bm25_phon12": "BM25, sound-alike key uni+bigrams",
    "restoretrain_bm25_syl12": "Restore$_{\\mathrm{train}}$ + uni+bigrams",
    "restoreall_bm25_syl": "Restore$_{\\mathrm{all}}$ + syllables",
    "restoreall_bm25_syl12": "Restore$_{\\mathrm{all}}$ + uni+bigrams",
    "bm25_meta": "BM25, title/artist/genre only",
    "bm25_lyrmeta_nodiac12": "BM25, lyrics + metadata",
    "lexall_equal": "RRF, equal weights",
    "lexall_global": "RRF, weights tuned on dev",
    "lexall_adaptive": "RRF, weights per predicted type",
    "lexall_oracle": "RRF, weights per true type (oracle)",
}
MODEL_LABELS = {"e5small": "mE5-small", "e5base": "mE5-base", "viemb": "Vietnamese\\_Embedding",
                "bgem3": "BGE-M3", "bkai": "bkai bi-encoder"}
RERANK_LABELS = {"bgererank": "bge-reranker-v2-m3", "vierank": "Vietnamese\\_Reranker"}
FUSION_LABELS = {"equal": "equal weights", "global": "weights tuned on dev",
                 "adaptive": "weights per predicted type", "oracle": "weights per true type (oracle)"}
PENDING = "\\pending{not run yet}"


# ------------------------------------------------------------------ data


def macro_f1(confusion: dict) -> float:
    """Mean F1 over the true classes of a confusion matrix {true: {predicted: count}}."""
    f1s = []
    for c, row in confusion.items():
        tp = row.get(c, 0)
        fp = sum(r.get(c, 0) for t, r in confusion.items() if t != c)
        fn = sum(row.values()) - tp
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return sum(f1s) / len(f1s) if f1s else 0.0

def load_queries(work):
    qs = []
    for f in QUERY_FILES:
        path = os.path.join(work, "queries", f)
        if os.path.exists(path):
            qs += read_jsonl(path)
    sweep = read_jsonl(os.path.join(work, "queries", "noise_sweep.jsonl"))
    return qs, sweep


def load_ranks(work, queries, cluster_of):
    """system -> qid -> rank of the target cluster (math.inf when not in the top 100)."""
    ranks = {}
    gz = os.path.join(work, "results", "perquery_ranks.json.gz")
    if os.path.exists(gz):
        with gzip.open(gz, "rt") as fh:
            for name, d in json.load(fh).items():
                ranks[name] = {q: (math.inf if r is None else r) for q, r in d.items()}
    runs_dir = os.path.join(work, "runs")
    if os.path.isdir(runs_dir):
        for f in sorted(os.listdir(runs_dir)):
            if not f.endswith(".trec") or f[:-5] in ranks:
                continue
            pq = per_query(read_trec(os.path.join(runs_dir, f)), queries, cluster_of)
            ranks[f[:-5]] = {q: v["rank"] for q, v in pq.items()}
    # a run made on a sample of queries (reranking) is evaluated on that sample only
    for name in list(ranks):
        qids_path = os.path.join(runs_dir, f"{name}.qids")
        if os.path.exists(qids_path):
            keep = set(json.load(open(qids_path)))
            ranks[name] = {q: r for q, r in ranks[name].items() if q in keep}
    return ranks


def metric(rank, m):
    if m == "rr10":
        return 1.0 / rank if rank <= 10 else 0.0
    if m == "s1":
        return float(rank == 1)
    if m == "s10":
        return float(rank <= 10)
    return float(rank <= 100)


class Scores:
    """Per-query metric vectors of every system on fixed query groups."""

    def __init__(self, ranks, groups):
        self.ranks, self.groups = ranks, groups

    def has(self, system, group=None):
        if system not in self.ranks:
            return False
        if group is None:
            return True
        return all(q in self.ranks[system] for q in self.groups.get(group, [])[:1])

    def vec(self, system, group, m="rr10", qids=None):
        r = self.ranks[system]
        qids = qids if qids is not None else self.groups[group]
        return np.array([metric(r.get(q, math.inf), m) for q in qids])

    def mean(self, system, group, m="rr10"):
        if not self.has(system) or not self.groups.get(group):
            return None
        qids = [q for q in self.groups[group] if q in self.ranks[system]]
        if not qids:
            return None
        return float(self.vec(system, group, m, qids).mean())

    def macro(self, system, m="rr10", types=TYPES):
        vals = [self.mean(system, t, m) for t in types if self.groups.get(t)]
        return None if not vals or any(v is None for v in vals) else float(np.mean(vals))


def mean_over_seeds(ranks, systems):
    """A pseudo-system whose per-query metric is the mean over seeds (for significance tests)."""
    return [ranks[s] for s in systems if s in ranks]


KIND_LABEL = {"other_word": "Another word", "tone": "Tone", "initial_known": "Initial, listed confusion",
              "initial_other": "Initial, other", "final": "Final consonant", "vowel_mark": "Vowel mark",
              "one_letter": "One letter (typing)"}
KIND_KEY = {"other_word": "OtherWord", "tone": "Tone", "initial_known": "InitKnown", "initial_other": "InitOther",
            "final": "Final", "vowel_mark": "VowelMark", "one_letter": "OneLetter"}


def sweep_kinds(sweep):
    """channel letter -> Counter of the kinds of the syllables it changed (all rates pooled), each
    changed syllable classified against the clean fragment like the observed substitutions."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from real_noise_stats import classify                                         # noqa: E402
    clean_of = {q["qid"].rsplit("-", 1)[0]: q["text"].split() for q in sweep if q["type"] == "N0"}
    out = defaultdict(Counter)
    for q in sweep:
        if q["type"] == "N0" or q["type"][0] not in "NEW":
            continue
        src, got = clean_of.get(q["qid"].rsplit("-", 1)[0]), q["text"].split()
        if not src or len(src) != len(got):
            continue
        for x, y in zip(src, got):
            if x != y:
                out[q["type"][0]][classify(x, y)] += 1
    return out


# ------------------------------------------------------------------ formatting

def f3(x):
    return "--" if x is None else f"{x:.3f}"


def esc(s):
    return s.replace("_", "\\_")


def dense_label(n):
    """Readable name of a dense run given without its dense_ prefix: the model, the NA-FT seed and
    whether the queries were restored first (suffix _rest)."""
    rest = n.endswith(REST_SUFFIX)
    if rest:
        n = n[:-len(REST_SUFFIX)]
    m = re.match(r"^naft_([a-z0-9]+)_s(\d+)$", n)
    lab = f"NA-FT {MODEL_LABELS.get(m.group(1), esc(m.group(1)))} (seed {m.group(2)})" if m else \
        MODEL_LABELS.get(n, esc(n))
    return lab + (" on restored queries" if rest else "")


def hybrid_label(h):
    """hybrid_<dense run>_<weights> -> readable name."""
    m = re.match(r"^hybrid_(.+)_(equal|global|adaptive|oracle)$", h)
    return f"hybrid with {dense_label(m.group(1))}, {FUSION_LABELS[m.group(2)]}" if m else LABELS.get(h, esc(h))


def sig_marks(scores, systems, base, types, alpha=0.05, n=10000):
    """Holm-corrected paired randomization tests of every system against `base`, per column.
    Returns marks[system][column] in {'', 'up', 'down'}."""
    marks = defaultdict(dict)
    if base not in scores.ranks:
        return marks
    for t in types:
        pvals, deltas = {}, {}
        for s in systems:
            if s == base or s not in scores.ranks or not scores.groups.get(t):
                continue
            qids = [q for q in scores.groups[t] if q in scores.ranks[s] and q in scores.ranks[base]]
            if not qids:
                continue
            a, b = scores.vec(s, t, "rr10", qids), scores.vec(base, t, "rr10", qids)
            pvals[s] = randomization_test(a, b, n)
            deltas[s] = a.mean() - b.mean()
        for s, p in holm(pvals).items():
            marks[s][t] = "" if p >= alpha else ("up" if deltas[s] > 0 else "down")
    return marks


def cell(value, mark="", best=False):
    txt = f3(value)
    if best and value is not None:
        txt = f"\\textbf{{{txt}}}"
    if mark == "up":
        txt += "$^{\\dagger}$"
    elif mark == "down":
        txt += "$^{\\ddagger}$"
    return txt


def table_rows(scores, groups, label_of, base, extra_marks=None):
    """Rows: system label, Q1..Q5 MRR@10, macro MRR@10, macro S@1, S@10, R@100."""
    systems = [s for _, ss in groups for s in ss]
    marks = sig_marks(scores, systems, base, TYPES)
    present = [s for s in systems if scores.has(s)]
    # runs over a sample of the queries (reranking) and oracle runs (true query type) do not compete
    # for the best value
    full = [s for s in present if not s.startswith(("rerank_", "firststage_")) and not s.endswith("_oracle")]
    best = {t: max((scores.mean(s, t) or -1 for s in full), default=None) for t in TYPES}
    best["macro"] = max((scores.macro(s) or -1 for s in full), default=None)
    lines = []
    ncol = 10
    for gname, ss in groups:
        lines.append(f"\\multicolumn{{{ncol}}}{{l}}{{\\textit{{{gname}}}}} \\\\")
        for s in ss:
            label = label_of(s)
            if not scores.has(s):
                lines.append(f"\\quad {label} & \\multicolumn{{{ncol - 1}}}{{c}}{{{PENDING}}} \\\\")
                continue
            cells = []
            for t in TYPES:
                v = scores.mean(s, t)
                cells.append(cell(v, marks.get(s, {}).get(t, ""), v is not None and s in full and abs(v - best[t]) < 1e-9))
            mac = scores.macro(s)
            cells.append(cell(mac, "", mac is not None and s in full and abs(mac - best["macro"]) < 1e-9))
            cells += [f3(scores.macro(s, m)) for m in ("s1", "s10", "r100")]
            ref = " (ref.)" if s == base else ""
            lines.append(f"\\quad {label}{ref} & " + " & ".join(cells) + " \\\\")
        lines.append("\\addlinespace")
    if lines and lines[-1] == "\\addlinespace":
        lines.pop()
    return lines


def write_table(path, caption, label, rows, note, n_by_type):
    head = " & ".join([f"Q{i}" for i in range(1, 6)])
    ns = " & ".join(f"\\scriptsize{{{n_by_type.get(t, 0)}}}" for t in TYPES)
    body = "\n".join(rows)
    tex = f"""\\begin{{table*}}[t]
\\centering
\\caption{{{caption}}}
\\label{{{label}}}
\\footnotesize
\\setlength{{\\tabcolsep}}{{3.3pt}}
\\begin{{tabular}}{{@{{}}l rrrrr r rrr@{{}}}}
\\toprule
 & \\multicolumn{{5}}{{c}}{{MRR@10 by query type}} & & \\multicolumn{{3}}{{c}}{{macro average}} \\\\
\\cmidrule(lr){{2-6}} \\cmidrule(l){{8-10}}
System & {head} & Macro & S@1 & S@10 & R@100 \\\\
 \\scriptsize{{queries}} & {ns} & & & & \\\\
\\midrule
{body}
\\bottomrule
\\end{{tabular}}

\\vspace{{2pt}}
\\parbox{{\\textwidth}}{{\\footnotesize {note}}}
\\end{{table*}}
"""
    open(path, "w").write(tex)


# ------------------------------------------------------------------ main

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="work_pilot")
    ap.add_argument("--out", default="paper/generated")
    ap.add_argument("--split", default="test")
    ap.add_argument("--prompts", default="docs/human_queries/human_prompts.json",
                    help="songs shown in the human-query form")
    a = ap.parse_args(argv)
    work, out = a.work, a.out
    os.makedirs(out, exist_ok=True)
    R = lambda f: os.path.join(work, "results", f)          # noqa: E731
    load_json = lambda f: json.load(open(R(f))) if os.path.exists(R(f)) else {}   # noqa: E731

    cluster_of = json.load(open(os.path.join(work, "clusters.json")))
    queries, sweep = load_queries(work)
    test = [q for q in queries if q["split"] == a.split]
    dev = [q for q in queries if q["split"] == "dev"]
    groups = defaultdict(list)
    for q in test:
        groups[q["type"]].append(q["qid"])
    for q in sweep:
        groups[q["type"]].append(q["qid"])
    dev_groups = defaultdict(list)
    for q in dev:
        dev_groups[q["type"]].append(q["qid"])
    ranks = load_ranks(work, queries + sweep, cluster_of)
    S = Scores(ranks, groups)
    D = Scores(ranks, dev_groups)
    n_by_type = {t: len(groups[t]) for t in TYPES}
    num = {}

    # ---------------- collection
    cs, cst, lp = load_json("collection_stats.json"), load_json("corpus_stats.json"), load_json("lexical_params.json")
    corpus = cst.get("corpus", {})
    num.update({
        "NSongsIn": corpus.get("input"), "NSongs": cs.get("songs"), "NClusters": cs.get("clusters"),
        "NTooShort": corpus.get("too_short"), "NNotVi": corpus.get("not_vietnamese"),
        "NPassages": cs.get("passages"),
        "NClustersMulti": sum(v for k, v in cs.get("songs_per_cluster", {}).items() if int(k) > 1),
        "NSongsInMulti": sum(int(k) * v for k, v in cs.get("songs_per_cluster", {}).items() if int(k) > 1),
        "LargestCluster": max((int(k) for k in cs.get("songs_per_cluster", {})), default=0),
        "MedianSyl": int(cs.get("syllables_per_song", {}).get("median", 0)),
        "MedianLines": int(cs.get("lines_per_song", {}).get("median", 0)),
        "MeanPassageSyl": f"{cs.get('passage_syllables', {}).get('mean', 0):.0f}",
        "NTrainSongs": cs.get("split_songs", {}).get("train"), "NDevSongs": cs.get("split_songs", {}).get("dev"),
        "NTestSongs": cs.get("split_songs", {}).get("test"),
        "NTrainClusters": cs.get("split_clusters", {}).get("train"),
        "NDevClusters": cs.get("split_clusters", {}).get("dev"),
        "NTestClusters": cs.get("split_clusters", {}).get("test"),
        "NWithArtist": cs.get("with_artist"), "NWithGenre": cs.get("with_genre"),
        "NTestQueries": len([q for q in test if q["type"] in TYPES]),
        "NDevQueries": len([q for q in dev if q["type"] in TYPES]),
        "NSweepQueries": len([q for q in sweep if q["type"] in SWEEP]), "NSweepFragments": len(groups.get("N0", [])),
        "NEmpSweepQueries": len([q for q in sweep if q["type"].startswith("E")]),
        "NLLMQueries": len([q for q in queries if q["type"] in ("Q4", "Q5")]),
        "NHumanQueries": len([q for q in queries if q["type"] == "H"]),
        "NHumanPrompts": len(json.load(open(a.prompts))) if os.path.exists(a.prompts) else None,
    })
    for src, n in cs.get("sources", {}).items():
        num["NSrc" + re.sub(r"[^A-Za-z]", "", src.split(".")[0]).capitalize()] = n
    sy = cs.get("syllables", {})
    if sy:
        num["NMarkedTypes"], num["NUnmarkedTypes"] = sy["marked_types"], sy["unmarked_types"]
        num["SylLettersMedian"] = f"{sy['letters_median']:g}"
        num["SylShareTwoFive"] = f"{100 * sy['share_2_to_5_letters']:.0f}"
        num["FormsPerUnmarked"] = f"{sy['marked_forms_per_unmarked_token']:.1f}"
        num["TypesPerTrigram"] = f"{sy['syllable_types_per_trigram_token']:.0f}"
        num["NFormsBo"] = len(sy.get("forms_of_bo", []))
    num["NClusterPairsLyric"] = cst.get("clusters", {}).get("lyric_pairs")
    num["NClusterPairsTitle"] = cst.get("clusters", {}).get("title_pairs")
    clu = cst.get("clusters", {})
    num.update({"NRomanised": corpus.get("romanised"), "NSplitJoins": corpus.get("split_syllables_joined"),
                "NDupId": corpus.get("dup_id"),
                "NCompTitle": clu.get("compilations_by_title"), "NCompLyrics": clu.get("compilations_by_lyrics"),
                "NCandidatePairs": clu.get("candidate_pairs"), "LSHBands": clu.get("lsh_bands"),
                "LSHRows": clu.get("lsh_rows"), "PCandidate": clu.get("p_candidate_at_threshold")})
    # Q4/Q5 of the pilot clusters versus the hash sample of the clusters the crawl added
    llm = [q for q in queries if q["type"] in ("Q4", "Q5")]
    num["NLLMPilot"] = sum(q.get("meta", {}).get("batch") == "pilot" for q in llm)
    num["NLLMSampled"] = len(llm) - num["NLLMPilot"]
    for sp, tag in (("dev", "Dev"), ("test", "Test")):
        num[f"NLLMClusters{tag}"] = len({q["cluster"] for q in llm if q["split"] == sp})
    org = load_json("corpus_origin.json")
    if org:
        num.update({"NUniform": org["uniform_crawl"], "NPilotSongs": org["pilot_songs"],
                    "NOnlyPilot": org["only_pilot"], "NOnlyPilotHac": org["only_pilot_by_source"].get("hopamchuan.com", 0),
                    "NPilotBoth": org["pilot_also_crawled"],
                    "UniformShare": f"{100 * org['uniform_crawl'] / max(1, org['final_songs']):.0f}"})
    crawl = load_json("crawl_stats.json")
    for k, tag in (("pages_fetched", "CrawlPages"), ("songs", "CrawlSongs"), ("errors", "CrawlErrors"),
                   ("start", "CrawlStart"), ("end", "CrawlEnd"), ("id_max", "CrawlIdMax")):
        if k in crawl:
            num[tag] = crawl[k]
    if crawl.get("songs") and corpus.get("input"):
        num["NCourseDB"] = corpus["input"] - crawl["songs"]
    ov = load_json("naft_overlap.json")
    if ov:
        num["NaftSnapshotSongs"] = ov["snapshot_train_songs"]
        num["NaftMovedSongs"] = ov["now_dev_or_test"]
        num["NaftMovedClusters"] = ov["clusters"]
        num["NaftDroppedQueries"] = sum(v for k, v in ov["queries_dropped"].items()
                                        if k in ("rule.jsonl", "llm.jsonl", "human.jsonl"))
    rr = sorted(f for f in os.listdir(os.path.join(work, "results")) if f.startswith("rerank_") and f.endswith("_stats.json"))
    if rr:
        st = json.load(open(R(rr[0])))
        num["RerankDepth"] = st.get("depth", 20)
        n_pass = st.get("passages_per_song", 2)
        num["RerankPassagesWord"] = "passage" if n_pass == 1 else f"{n_pass} passages".replace("2 ", "two ")
        num["RerankQueries"] = st["queries"]
        num["RerankPairsPerSec"] = f"{st['pairs'] / max(1e-9, st['seconds']):.1f}"
        num["RerankSecPerQuery"] = f"{st['seconds'] / max(1, st['queries']):.1f}"
    eff = load_json("efficiency.json")
    for n, row in eff.get("dense", {}).items():
        tag = re.sub(r"[^A-Za-z]", "", n.capitalize())
        num[f"EffPassPerSec{tag}"] = f"{row['passages_per_s']:.0f}"
        num[f"EffQueryMs{tag}"] = f"{row['ms_per_query_encode']:.0f}"
        if "ms_per_query_search_single" in row:
            num[f"EffSearchMs{tag}"] = f"{row['ms_per_query_search_single']:.0f}"
            num[f"EffEncodeMin{tag}"] = f"{row['full_encode_minutes']:.0f}"
            num[f"EffIndexMB{tag}"] = f"{row['index_mb']:.0f}"
    for an, row in eff.get("lexical", {}).items():
        tag = re.sub(r"[^A-Za-z]", "", an.capitalize())
        num[f"EffLexMs{tag}"] = f"{row['ms_per_query']:.1f}"
        num[f"EffLexIndexS{tag}"] = f"{row['index_s']:.0f}"
    infos = []
    for d in sorted(os.listdir(os.path.join(work, "models"))) if os.path.isdir(os.path.join(work, "models")) else []:
        f = os.path.join(work, "models", d, "naft_info.json")
        if os.path.exists(f):
            infos.append(json.load(open(f)))
    if infos:
        num["NaftExamples"] = max(i["examples"] for i in infos)
        num["NaftTrainMinutes"] = f"{np.mean([i['train_seconds'] for i in infos]) / 60:.0f}"
        kinds = infos[0].get("kinds", {})
        num["NaftIctShare"] = f"{100 * kinds.get('ICT', 0) / max(1, sum(kinds.values())):.0f}"

    qrows = []
    for t in TYPES + UTYPES + VARIANTS:
        st = cs.get("queries", {}).get(f"{t}/{a.split}")
        sd = cs.get("queries", {}).get(f"{t}/dev", {})
        str_ = cs.get("queries", {}).get(f"{t}/train", {})
        if not st:
            continue
        desc = {"Q1": "exact fragment", "Q2": "unmarked fragment", "Q3": "misheard / mistyped",
                "Q4": "paraphrase (LLM)", "Q5": "description (LLM)", "Q4u": "Q4 typed without marks",
                "Q5u": "Q5 typed without marks", "Q3u": "Q3 typed without marks",
                "Q4s": "short paraphrase, other prompt", "Q5g": "description from a gist", "H": "human"}[t]
        ntrain = str_.get("n", 0)
        qrows.append(" & ".join([
            t, desc, str(ntrain or "--"), str(sd.get("n", 0)), str(st["n"]),
            f"{st['length']['mean']:.1f}", f"{st['lyric_overlap']['mean']:.2f}",
            f"{st['overlap_with_random_song']['mean']:.2f}", f"{st['longest_copied_run']['mean']:.1f}",
            f"{100 * st['share_without_marks']:.0f}\\%"]) + " \\\\")
    tex = f"""\\begin{{table*}}[t]
\\centering
\\caption{{Query sets. Lengths in syllables. \\emph{{Overlap}}: share of the query's unmarked syllables that occur in
the target song's lyrics, and in the lyrics of a random other song. \\emph{{Run}}: longest run of consecutive query
syllables copied from the lyrics. \\emph{{Unmarked}}: share of queries typed without any diacritic. Statistics on the
{a.split} split.}}
\\label{{tab:queries}}
\\footnotesize
\\setlength{{\\tabcolsep}}{{4.5pt}}
\\begin{{tabular}}{{@{{}}llrrrrrrrr@{{}}}}
\\toprule
 & & \\multicolumn{{3}}{{c}}{{queries}} & & \\multicolumn{{2}}{{c}}{{overlap}} & & \\\\
\\cmidrule(lr){{3-5}} \\cmidrule(lr){{7-8}}
Type & Description & train & dev & test & Length & target & random & Run & Unmarked \\\\
\\midrule
{chr(10).join(qrows)}
\\bottomrule
\\end{{tabular}}
\\end{{table*}}
"""
    open(os.path.join(out, "tab_queries.tex"), "w").write(tex)
    q3 = cs.get("queries", {}).get(f"Q3/{a.split}", {})
    ops = q3.get("noise_ops", {})
    tot = sum(ops.values()) or 1
    for op in ("initial", "final", "tone", "typo"):
        num[f"OpShare{op.capitalize()}"] = f"{100 * ops.get(op, 0) / tot:.0f}"
    num["QthreeChanged"] = f"{100 * q3.get('changed_share', {}).get('mean', 0):.0f}"
    for t in TYPES + VARIANTS:
        st = cs.get("queries", {}).get(f"{t}/{a.split}", {})
        if st:
            key = {"Q1": "One", "Q2": "Two", "Q3": "Three", "Q4": "Four", "Q5": "Five", "Q3u": "ThreeU",
                   "Q4s": "FourS", "Q5g": "FiveG"}[t]
            num[f"NTest{key}"] = st["n"]
            if st.get("share_naming_artist") is not None:
                num[f"ArtistShare{key}"] = f"{100 * st['share_naming_artist']:.0f}"
            num[f"Len{key}"] = f"{st['length']['mean']:.1f}"
            num[f"Overlap{key}"] = f"{st['lyric_overlap']['mean']:.2f}"
            num[f"RandOverlap{key}"] = f"{st['overlap_with_random_song']['mean']:.2f}"
            num[f"Run{key}"] = f"{st['longest_copied_run']['mean']:.1f}"
    q5 = cs.get("queries", {}).get(f"Q5/{a.split}", {})
    if q5.get("share_naming_artist") is not None:
        num["QfiveArtistShare"] = f"{100 * q5['share_naming_artist']:.0f}"
        num["QfiveFirstWord"] = q5["first_word"]["word"]
        num["QfiveFirstShare"] = f"{100 * q5['first_word']['share']:.0f}"

    # ---------------- lexical results
    base = "bm25_syl"
    rows = table_rows(S, LEXICAL_GROUPS, lambda s: LABELS.get(s, esc(s)), base)
    write_table(os.path.join(out, "tab_lexical.tex"),
                "Lexical retrieval on the test split (MRR@10).",
                "tab:lexical", rows,
                "$\\dagger$/$\\ddagger$: significantly better/worse than BM25 on syllables (paired randomization test, "
                "10{,}000 permutations, Holm correction over the systems of the table within each column, $p<0.05$). "
                "Bold: best value per column, oracle rows excluded. "
                "Macro: unweighted mean over the five query types. Restore$_{\\mathrm{all}}$ trains the diacritic "
                "restoration language model on every indexed song, Restore$_{\\mathrm{train}}$ on training songs only.",
                n_by_type)

    def m(system, t="macro", mm="rr10"):
        v = S.macro(system, mm) if t == "macro" else S.mean(system, t, mm)
        return None if v is None else v
    key_of = {"Q1": "One", "Q2": "Two", "Q3": "Three", "Q4": "Four", "Q5": "Five", "macro": "Macro",
              "Q4u": "FourU", "Q5u": "FiveU", "Q3u": "ThreeU", "Q4s": "FourS", "Q5g": "FiveG"}
    short = {"bm25_syl": "Syl", "bm25_syl12": "SylBi", "bm25_word": "Word", "ql_syl": "QL", "rm3_syl": "RMthree",
             "bm25_nodiac": "Nodiac", "bm25_nodiac12": "NodiacBi", "rm3_nodiac": "RMthreeNodiac",
             "bm25_char3": "Char", "bm25_char4x": "CharFourX", "bm25_char5x": "CharFiveX", "bm25_phon12": "Phon",
             "rm3_syl12": "RMthreeBi",
             "restoretrain_bm25_syl12": "RestTrainBi", "restoreall_bm25_syl": "RestAllSyl",
             "restoreall_bm25_syl12": "RestAllBi", "bm25_meta": "Meta", "bm25_lyrmeta_nodiac12": "LyrMeta",
             "lexall_equal": "FuseEqual", "lexall_global": "FuseGlobal", "lexall_adaptive": "FuseAdaptive",
             "lexall_oracle": "FuseOracle"}
    for s, k in short.items():
        for t, tk in key_of.items():
            v = m(s, t)
            if v is not None:
                num[f"M{k}{tk}"] = f"{v:.3f}"
        for nt, word in zip(SWEEP, ("Zero", "Ten", "Twenty", "Thirty", "Forty", "Fifty")):
            v = S.mean(s, nt)
            if v is not None:
                num[f"M{k}N{word}"] = f"{v:.3f}"
            for ch in ("E", "W"):
                v = S.mean(s, ch + nt[1:])
                if v is not None and nt != "N0":
                    num[f"M{k}{ch}{word}"] = f"{v:.3f}"
    lex_present = [s for _, ss in LEXICAL_GROUPS for s in ss if S.has(s)]
    if lex_present:
        best_q4 = max(lex_present, key=lambda s: S.mean(s, "Q4") or -1)
        num["BestLexQfour"] = f"{S.mean(best_q4, 'Q4'):.3f}"
        num["BestLexQfourName"] = LABELS.get(best_q4, esc(best_q4))
        single = [s for s in lex_present if not s.startswith("lex")]
        best_single = max(single, key=lambda s: D.macro(s) or -1)
        num["BestSingleLexDev"] = LABELS.get(best_single, esc(best_single))
        num["BestSingleLexMacro"] = f"{S.macro(best_single):.3f}"

    # restoration and classifier
    for k, nm in (("restore_all", "RestoreAccAll"), ("restore_train", "RestoreAccTrain")):
        if k in lp:
            num[nm] = f"{100 * lp[k]['q2_syllable_acc']:.1f}"
            num[nm.replace("Acc", "Ms")] = f"{lp[k]['ms_per_query']:.1f}"
            for t, tk in (("q4u", "FourU"), ("q5u", "FiveU")):      # unmarked paraphrases / descriptions
                if f"{t}_syllable_acc" in lp[k]:
                    num[nm + tk] = f"{100 * lp[k][f'{t}_syllable_acc']:.1f}"
    for k, nm in (("qtype_logreg", "QtypeAccLR"), ("qtype_nb", "QtypeAccNB")):
        if k in lp and lp[k].get("test_accuracy") is not None:
            num[nm] = f"{100 * lp[k]['test_accuracy']:.1f}"
    if "qtype_logreg" in lp:
        conf = lp["qtype_logreg"]["confusion"]
        classes = TYPES + [t for t in UTYPES if t in conf]
        trow = []
        for t in classes:
            row = conf.get(t, {})
            n = sum(row.values()) or 1
            trow.append(f"{t} & " + " & ".join(f"{row.get(c, 0)}" for c in classes) + f" & {100 * row.get(t, 0) / n:.0f}\\% \\\\")
        nc = len(classes)
        open(os.path.join(out, "tab_qtype.tex"), "w").write(f"""\\begin{{table}}[t]
\\centering
\\caption{{Confusion matrix of the query-type classifier (logistic regression) on the test queries. Rows: true type;
columns: predicted type. Accuracy {100 * lp['qtype_logreg']['test_accuracy']:.1f}\\%
(Naive Bayes: {100 * lp['qtype_nb']['test_accuracy']:.1f}\\%). Q4u and Q5u are Q4 and Q5 typed without marks.}}
\\label{{tab:qtype}}
\\small
\\begin{{tabular}}{{@{{}}l{'r' * nc}r@{{}}}}
\\toprule
 & \\multicolumn{{{nc}}}{{c}}{{predicted}} & \\\\
\\cmidrule(lr){{2-{nc + 1}}}
true & {' & '.join(classes)} & recall \\\\
\\midrule
{chr(10).join(trow)}
\\bottomrule
\\end{{tabular}}
\\end{{table}}
""")
        for t in classes:
            row = conf.get(t, {})
            n = sum(row.values()) or 1
            num[f"QtypeRecall{key_of[t]}"] = f"{100 * row.get(t, 0) / n:.0f}"
        num["QtypeTrainFour"] = lp["qtype_logreg"]["train_examples"].get("Q4")
        te = lp["qtype_logreg"]["train_examples"]
        if te.get("Q1") and te.get("Q4"):
            num["QtypeTrainRule"] = te["Q1"]
            num["QtypeTrainRatio"] = f"{te['Q1'] / te['Q4']:.0f}"
    for k, nm in (("qtype_logreg", "LR"), ("qtype_nb", "NB")):
        if k in lp:
            num[f"QtypeMacroF{nm}"] = f"{macro_f1(lp[k]['confusion']):.3f}"
            cv = lp[k].get("cv_macro_f1")
            if cv:
                num[f"QtypeCvPlain{nm}"] = f"{cv['plain']:.3f}"
                num[f"QtypeCvBalanced{nm}"] = f"{cv['balanced']:.3f}"
    # text-mining analyses (scripts/tmg_analyses.py): restoration by n-gram order, unweighted classifier
    ta_path = os.path.join(work, "results", "tmg_analyses.json")
    if os.path.exists(ta_path):
        ta = json.load(open(ta_path))
        for lm, lmk in (("all", "All"), ("train", "Train")):
            for order, ok in ((1, "One"), (2, "Two"), (3, "Three")):
                v = ta.get("restore_order", {}).get(f"{lm}_{order}", {}).get("Q2")
                if v:
                    num[f"RestoreOrder{lmk}{ok}"] = f"{100 * v['syllable_acc']:.1f}"
        plain = ta.get("qtype", {}).get("logreg_plain")
        if plain:
            num["QtypeRecallFourPlain"] = f"{100 * plain['per_class']['Q4']['recall']:.0f}"
            num["QtypeAccLRPlain"] = f"{100 * plain['accuracy']:.1f}"
        fc = ta.get("fragment_check")
        if fc:          # test fragments that also occur verbatim in another cluster (exact check)
            num["NAmbigFragments"], num["NAmbigQueries"] = fc["ambiguous_fragments"], fc["ambiguous_queries"]
            best = fc["runs"].get("restoreall_bm25_syl12")
            if best:
                num["NOtherSongFirst"] = best["other_song_first"]
                num["PassageGain"] = f"{max(best['passage'][t] - best['strict'][t] for t in ('Q1', 'Q2', 'Q3')):.3f}"
    for k in ("bm25_syl", "bm25_syl12", "bm25_word", "bm25_nodiac", "bm25_nodiac12", "bm25_char3", "bm25_char4x",
              "bm25_char5x", "bm25_phon12", "bm25_meta"):
        if k in lp and "k1" in lp[k]:
            num[f"Kone{short[k]}"] = lp[k]["k1"]
            num[f"B{short[k]}"] = lp[k]["b"]
    if "ql_syl" in lp:
        num["QLmu"] = lp["ql_syl"]["mu"]
    if "bm25_lyrmeta_nodiac12" in lp:
        num["MetaWeight"] = lp["bm25_lyrmeta_nodiac12"]["meta_weight"]

    # fusion weights
    fp = load_json("fusion_params.json")
    if "lexall" in fp:
        g = fp["lexall"]["global"]
        num["FuseGlobalWeights"] = "(" + ", ".join(f"{w:g}" for w in g["weights"]) + ")"
        used = [t for t, v in fp["lexall"]["per_type"].items() if v.get("use_type")]
        num["FuseTypesKept"] = ", ".join(used) if used else "none"
        num["NFuseTypesKept"] = len(used)
        for t, tk in (("Q3", "Qthree"), ("Q4", "Qfour")):
            pt = fp["lexall"]["per_type"].get(t)
            if pt:   # noisy fragments and paraphrases: the types whose own weights passed the guard
                num[f"Fuse{tk}Weights"] = "(" + ", ".join(f"{w:g}" for w in pt["weights"]) + ")"
                num[f"Fuse{tk}CvType"] = f"{pt['cv_type']:.3f}"
                num[f"Fuse{tk}CvGlobal"] = f"{pt['cv_global']:.3f}"
    if "lextrain" in fp:
        used = [t for t, v in fp["lextrain"]["per_type"].items() if v.get("use_type")]
        num["FuseTrainTypesKept"] = ", ".join(used) if used else "none"

    # ---------------- neural results
    systems = list(ranks)
    zero = sorted({m_.group(1) for s in systems if (m_ := re.match(r"^dense_(?!naft_)([a-z0-9]+)$", s))})
    # each zero-shot and NA-FT model is also run on diacritic-restored queries (suffix _rest)
    zero_runs = [r for n in zero for r in (f"dense_{n}", f"dense_{n}{REST_SUFFIX}") if r in ranks]
    naft = defaultdict(list)
    naft_rest = defaultdict(list)
    ablation = defaultdict(list)
    for s in systems:
        m1 = re.match(r"^dense_naft_([a-z0-9]+)_s(\d+)$", s)
        m2 = re.match(r"^dense_naft_([a-z0-9]+)_(nonoise|randneg)_s(\d+)$", s)
        m3 = re.match(rf"^dense_naft_([a-z0-9]+)_s(\d+){REST_SUFFIX}$", s)
        if m1:
            naft[m1.group(1)].append(s)
        elif m2:
            ablation[(m2.group(1), m2.group(2))].append(s)
        elif m3:
            naft_rest[m3.group(1)].append(s)
    hybrids = defaultdict(dict)
    for s in systems:
        mh = re.match(r"^hybrid_(.+)_(equal|global|adaptive|oracle)$", s)
        if mh:
            hybrids[mh.group(1)][mh.group(2)] = s
    reranks = [s for s in systems if s.startswith("rerank_")]

    lex_ref = "lexall_global" if S.has("lexall_global") else base
    neural_groups = []
    labels = {}
    expected_zero = list(MODEL_LABELS)
    zs = []
    for n in (zero or expected_zero):
        labels[f"dense_{n}"] = MODEL_LABELS.get(n, esc(n))
        labels[f"dense_{n}{REST_SUFFIX}"] = "\\quad on restored queries"
        zs += [f"dense_{n}", f"dense_{n}{REST_SUFFIX}"]
    neural_groups.append(("Dense, zero-shot (MaxP over 4-line passages)", zs))
    # seed-averaged NA-FT as pseudo-systems
    seed_rows = []
    for b in sorted(naft) or ["e5small", "e5base"]:
        for suffix, runs_of in (("", naft), (REST_SUFFIX, naft_rest)):
            members = sorted(runs_of.get(b, []))
            name = f"naftmean_{b}{suffix}"
            seeds_note = f" ({len(members)} seed{'s' if len(members) > 1 else ''})" if members else ""
            labels[name] = (f"NA-FT {MODEL_LABELS.get(b, esc(b))}" if not suffix else "\\quad on restored queries") \
                + seeds_note
            if members:
                per = {}
                for q in set.intersection(*(set(ranks[s]) for s in members)):
                    # pseudo-rank whose RR@10 equals the seed mean is not defined; keep the per-seed ranks
                    per[q] = [ranks[s][q] for s in members]
                ranks[name] = {q: v for q, v in per.items()}
            seed_rows.append(name)
    for (b, kind), members in sorted(ablation.items()):
        name = sorted(members)[0]
        labels[name] = f"\\quad without {'noise' if kind == 'nonoise' else 'hard negatives'} (seed {name[-1]})"
        seed_rows.append(name)
    neural_groups.append(("Noise-aware fine-tuning (NA-FT)", seed_rows))
    for h in sorted(hybrids):
        hs = [hybrids[h][v] for v in ("equal", "global", "adaptive", "oracle") if v in hybrids[h]]
        for v in ("equal", "global", "adaptive", "oracle"):
            if v in hybrids[h]:
                labels[hybrids[h][v]] = f"RRF, {FUSION_LABELS[v]}"
        neural_groups.append((f"Hybrid: lexical lists + {dense_label(h).replace(' on restored queries', ', restored queries')}", hs))
    if not hybrids:
        neural_groups.append(("Hybrid: lexical lists + best dense model", ["hybrid_pending"]))
    # every reranker rescores the same first-stage run (the best hybrid on dev): name it once
    firsts = sorted({re.match(r"^rerank_([a-z0-9]+)_(.+)$", r).group(2) for r in reranks})
    for r in reranks:
        mr = re.match(r"^rerank_([a-z0-9]+)_(.+)$", r)
        labels[r] = RERANK_LABELS.get(mr.group(1), esc(mr.group(1))) + \
            ("" if len(firsts) == 1 else f", top 20 of {hybrid_label(mr.group(2))}")
    rerank_rows = list(reranks)
    if len(firsts) == 1 and firsts[0] in ranks and reranks:
        # the first-stage run on the same sampled queries, so that the reranking gain can be read off
        ranks["firststage_sample"] = {q: ranks[firsts[0]][q] for q in ranks[reranks[0]] if q in ranks[firsts[0]]}
        labels["firststage_sample"] = "first stage, same queries"
        rerank_rows = ["firststage_sample"] + rerank_rows
    neural_groups.append(("Cross-encoder reranking of the top 20 of the selected hybrid" if len(firsts) == 1
                          else "Cross-encoder reranking", rerank_rows or ["rerank_pending"]))
    labels["hybrid_pending"] = "best hybrid"
    labels["rerank_pending"] = "cross-encoder on the best hybrid"

    class SeedAware(Scores):
        def vec(self, system, group, m="rr10", qids=None):
            r = self.ranks[system]
            qids = qids if qids is not None else self.groups[group]
            out = []
            for q in qids:
                v = r.get(q, math.inf)
                out.append(np.mean([metric(x, m) for x in v]) if isinstance(v, list) else metric(v, m))
            return np.array(out)

    SN = SeedAware(ranks, groups)
    neural_rows = [f"\\multicolumn{{10}}{{l}}{{\\textit{{Reference}}}} \\\\"]
    ref_cells = [f3(SN.mean(lex_ref, t)) for t in TYPES] + [f3(SN.macro(lex_ref))] + \
                [f3(SN.macro(lex_ref, mm)) for mm in ("s1", "s10", "r100")]
    neural_rows.append(f"\\quad {LABELS.get(lex_ref, esc(lex_ref))} & " + " & ".join(ref_cells) + " \\\\")
    neural_rows.append("\\addlinespace")
    neural_rows += table_rows(SN, neural_groups, lambda s: labels.get(s, esc(s)), lex_ref)
    write_table(os.path.join(out, "tab_neural.tex"),
                "Dense, fine-tuned, hybrid and reranked retrieval on the test split (MRR@10). Every choice of model "
                "or weights was made on the dev split.",
                "tab:neural", neural_rows,
                f"$\\dagger$/$\\ddagger$: significantly better/worse than the reference lexical system "
                f"({LABELS.get(lex_ref, esc(lex_ref))}; paired randomization test, Holm correction within each column, "
                "$p<0.05$). Bold: best value per column among runs over every query, oracle rows excluded. NA-FT rows "
                "average the per-query scores of the seeds before testing. Reranking rows are evaluated on the "
                "queries that were reranked.", n_by_type)
    num["LexRefName"] = LABELS.get(lex_ref, esc(lex_ref))
    num["NeuralDone"] = "1" if zero else "0"
    letters = lambda x: re.sub(r"[^A-Za-z]", "", x.capitalize())                       # noqa: E731
    neural_keys = {}
    for n in zero:
        neural_keys[f"dense_{n}"] = f"Dense{letters(n)}"
        neural_keys[f"dense_{n}{REST_SUFFIX}"] = f"Dense{letters(n)}Rest"
    for b in naft:
        neural_keys[f"naftmean_{b}"] = "Naft" if b == "e5small" else f"Naft{letters(b)}"
        neural_keys[f"naftmean_{b}{REST_SUFFIX}"] = neural_keys[f"naftmean_{b}"] + "Rest"
        for r in naft[b]:
            neural_keys[r] = neural_keys[f"naftmean_{b}"] + "S" + {"1": "one", "2": "two", "3": "three"}.get(r[-1], r[-1])
    for (b, kind), members in ablation.items():
        neural_keys[sorted(members)[0]] = "Naft" + kind.capitalize()
    for h, d in hybrids.items():
        tag = "HybEbase" if h.startswith("e5base") else "HybNaft" if h.startswith("naft") else "Hyb" + letters(h)
        for v, r in d.items():
            neural_keys[r] = tag + v.capitalize()
    for r in reranks:
        neural_keys[r] = "Rerank"
    if "firststage_sample" in ranks:
        neural_keys["firststage_sample"] = "FirstSample"
    for sname, k in neural_keys.items():
        if sname not in ranks:
            continue
        for t, tk in key_of.items():
            v = SN.macro(sname) if t == "macro" else SN.mean(sname, t)
            if v is not None:
                num[f"M{k}{tk}"] = f"{v:.3f}"
        for nt in ("N50", "E50"):
            v = SN.mean(sname, nt)
            if v is not None:
                num[f"M{k}{nt[0]}Fifty"] = f"{v:.3f}"
    for sname, k in neural_keys.items():
        if sname in ranks and not sname.startswith(("naftmean_", "firststage_", "rerank_")):
            v = D.macro(sname)
            if v is not None:
                num[f"M{k}Dev"] = f"{v:.3f}"
    # spread of NA-FT over seeds: macro MRR@10 of each seed
    for b in naft:
        for suffix, runs_of in (("", naft), (REST_SUFFIX, naft_rest)):
            vals = [S.macro(r) for r in sorted(runs_of.get(b, [])) if S.macro(r) is not None]
            if len(vals) > 1:
                k = neural_keys[f"naftmean_{b}{suffix}"]
                num[f"{k}SeedSd"] = f"{float(np.std(vals, ddof=1)):.3f}"
                num[f"{k}SeedMin"], num[f"{k}SeedMax"] = f"{min(vals):.3f}", f"{max(vals):.3f}"
                num[f"{k}Seeds"] = len(vals)

    # ---------------- noise figure and table
    # the selected hybrid: best dev macro among the hybrids, as chosen by run_gpu_experiments.py
    man = load_json("gpu_manifest.json")
    hyb_dev = {k: v for k, v in man.get("dev_macro_mrr10", {}).items() if k.startswith("hybrid_") and k in ranks}
    hy_ad = [d["adaptive"] for d in hybrids.values() if "adaptive" in d]
    SEL = max(hyb_dev, key=hyb_dev.get) if hyb_dev else (max(hy_ad, key=lambda x: D.macro(x) or -1) if hy_ad else None)
    if SEL:
        num["SelName"] = hybrid_label(SEL)
        num["SelDevMacro"] = f"{hyb_dev.get(SEL, D.macro(SEL) or 0):.3f}"
    rates = (10, 20, 30, 40, 50)
    rwords = dict(zip(rates, ("Ten", "Twenty", "Thirty", "Forty", "Fifty")))
    realised = defaultdict(list)
    for q in sweep:
        mt = q.get("meta", {})
        if mt.get("length") and q["type"] != "N0":
            realised[q["type"]].append(mt.get("changed", 0) / mt["length"])
    for r_ in rates:
        for ch in "NEW":
            if realised.get(f"{ch}{r_}"):
                num[f"Realised{ch}{rwords[r_]}"] = f"{100 * np.mean(realised[f'{ch}{r_}']):.0f}"
    sweep_cols = [t for t in groups if re.match(r"^[NEW]\d+$", t)]
    same_as_lex = bool(SEL) and SN.has(SEL) and SN.has("lexall_global") and all(
        abs((SN.mean(SEL, t) or 0) - (SN.mean("lexall_global", t) or 0)) < 1e-9 for t in sweep_cols)
    num["SelSameAsLexOnSweep"] = "1" if same_as_lex else "0"
    curve_systems = [("bm25_syl", "BM25, syllables"), ("bm25_nodiac12", "BM25, unmarked uni+bigrams"),
                     ("bm25_char5x", "BM25, char. 5-grams across syllables"),
                     ("restoreall_bm25_syl12", "Restore + uni+bigrams"),
                     ("lexall_global", "Lexical RRF = selected hybrid" if same_as_lex else "Lexical RRF")]
    if f"naftmean_e5small{REST_SUFFIX}" in ranks:
        curve_systems.append((f"naftmean_e5small{REST_SUFFIX}", "NA-FT mE5-small, restored"))
    if SEL and not same_as_lex:
        curve_systems = [c for c in curve_systems if c[0] != "bm25_char5x"] + [(SEL, "Selected hybrid")]
    table_systems = [("bm25_syl", "BM25, syllables"), ("bm25_nodiac12", "BM25, unmarked uni+bigrams"),
                     ("bm25_char3", "BM25, char.\\ 3-grams in syllables"),
                     ("bm25_char5x", "BM25, char.\\ 5-grams across syllables"),
                     ("bm25_phon12", "BM25, sound-alike key"),
                     ("restoreall_bm25_syl12", "Restore + uni+bigrams"), ("lexall_global", "Lexical RRF")]
    if zero_runs:      # the best zero-shot run on dev, with the queries as typed or restored
        best_zero = max(zero_runs, key=lambda s: D.macro(s) or -1)
        table_systems.append((best_zero, dense_label(best_zero[len('dense_'):]).replace(" on restored queries", ", restored")))
    if f"naftmean_e5small{REST_SUFFIX}" in ranks:
        table_systems.append((f"naftmean_e5small{REST_SUFFIX}", "NA-FT mE5-small, restored"))
    if SEL:
        table_systems.append((SEL, "Selected hybrid"))
    has_w = bool(groups.get("W50"))
    cols = ["N0"] + [f"N{r_}" for r_ in rates] + ([f"W{r_}" for r_ in rates] if has_w else []) + \
        (["E50"] if groups.get("E50") else [])
    nrows = []
    for s, lab in table_systems:
        if SN.has(s):
            nrows.append(lab + " & " + " & ".join(f3(SN.mean(s, t)) for t in cols) + " \\\\")
    real_row = "realised (\\%) & 0 & " + " & ".join(num.get(f"RealisedN{rwords[r_]}", "--") for r_ in rates)
    if has_w:
        real_row += " & " + " & ".join(num.get(f"RealisedW{rwords[r_]}", "--") for r_ in rates)
    if "E50" in cols:
        real_row += " & " + num.get("RealisedEFifty", "--")
    real_row += " \\\\"
    whead = " & \\multicolumn{5}{c}{observed channel (W)}" if has_w else ""
    wrule = " \\cmidrule(lr){8-12}" if has_w else ""
    ehead = " & \\multicolumn{1}{c}{E}" if "E50" in cols else ""
    nominal = " & ".join(f"{r_}\\%" for r_ in rates)
    open(os.path.join(out, "tab_noise.tex"), "w").write(f"""\\begin{{table*}}[t]
\\centering
\\caption{{MRR@10 on the noise sweep: the same {len(groups.get('N0', []))} test fragments with a growing share of
corrupted syllables, under the hand-set channel (N), the channel sampled from the variation observed between
transcriptions (W) and the hand-set operations re-weighted by the observed shares (E, 50\\% only;
Section~\\ref{{sec:validity}}). Columns give the nominal rate; the realised share of changed syllables is in the
last row. NA-FT: mean over three seeds.}}
\\label{{tab:noise}}
\\footnotesize
\\setlength{{\\tabcolsep}}{{3.4pt}}
\\begin{{tabular}}{{@{{}}l{'r' * len(cols)}@{{}}}}
\\toprule
 & & \\multicolumn{{5}}{{c}}{{hand-set channel (N)}}{whead}{ehead} \\\\
\\cmidrule(lr){{3-7}}{wrule}
System & 0\\% & {nominal}{(' & ' + nominal) if has_w else ''}{' & 50\\%' if 'E50' in cols else ''} \\\\
\\midrule
{chr(10).join(nrows)}
\\midrule
{real_row}
\\bottomrule
\\end{{tabular}}
\\end{{table*}}
""")
    plot_noise(SN, curve_systems, os.path.join(out, "fig_noise.pdf"), channels=("N", "W") if has_w else ("N",))

    # ---------------- paraphrases and descriptions typed without marks (Q4u, Q5u)
    urows = []
    usystems = [("bm25_syl", None), ("bm25_syl12", None), ("ql_syl", None), ("bm25_nodiac12", None),
                ("restoretrain_bm25_syl12", None), ("restoreall_bm25_syl12", None), ("bm25_lyrmeta_nodiac12", None),
                ("lexall_adaptive", None)]
    if zero:      # the best zero-shot model on dev, with the queries as typed and restored
        bz = max((f"dense_{n}" for n in zero), key=lambda x: D.macro(x) or -1)
        for r in (bz, bz + REST_SUFFIX):
            if r in ranks:
                usystems.append((r, f"Dense {dense_label(r[len('dense_'):])}"))
    hy = [f"{h}_adaptive" for h in (hybrids or {}) if f"{h}_adaptive" in ranks]
    for h in hy:
        lab = hybrid_label(h)
        usystems.append((h, lab[0].upper() + lab[1:]))
    pv = {"Q4u": {}, "Q5u": {}}
    for t in ("Q4u", "Q5u"):
        for sname, _ in usystems:
            if not S.has(sname) or not groups.get(t):
                continue
            pairs = [(qu, qu.replace(f"-{t}-", f"-{t[:-1]}-")) for qu in groups[t]]
            pairs = [(a_, b_) for a_, b_ in pairs if a_ in ranks[sname] and b_ in ranks[sname]]
            if pairs:
                a_ = np.array([metric(ranks[sname][x], "rr10") for x, _ in pairs])
                b_ = np.array([metric(ranks[sname][y], "rr10") for _, y in pairs])
                pv[t][sname] = (randomization_test(a_, b_), a_.mean() - b_.mean())
    adj = {t: holm({k: v[0] for k, v in pv[t].items()}) for t in pv}
    for sname, lab in usystems:
        if not S.has(sname):
            continue
        cells = []
        for t in ("Q4", "Q4u", "Q5", "Q5u"):
            v = S.mean(sname, t)
            mark = ""
            if t in adj and sname in adj[t] and adj[t][sname] < 0.05:
                mark = "$^{\\ddagger}$" if pv[t][sname][1] < 0 else "$^{\\dagger}$"
            cells.append(f3(v) + mark)
        urows.append(f"{lab or LABELS.get(sname, esc(sname))} & " + " & ".join(cells) + " \\\\")
    if groups.get("Q4u"):
        open(os.path.join(out, "tab_unmarked.tex"), "w").write(f"""\\begin{{table}}[t]
\\centering
\\caption{{Paraphrases (Q4) and descriptions (Q5) as written, with marks, and typed without marks (Q4u, Q5u):
the same queries with every mark removed. MRR@10 on the test split. $\\ddagger$/$\\dagger$: the unmarked
version is significantly worse/better for that system (paired randomization test, Holm correction within
each column, $p<0.05$).}}
\\label{{tab:unmarked}}
\\small
\\begin{{tabular}}{{@{{}}lrrrr@{{}}}}
\\toprule
System & Q4 & Q4u & Q5 & Q5u \\\\
\\midrule
{chr(10).join(urows)}
\\bottomrule
\\end{{tabular}}
\\end{{table}}
""")
        best_u = max((x for x, _ in usystems if S.has(x) and not x.startswith(("dense_", "hybrid_"))),
                     key=lambda x: S.mean(x, "Q4u") or -1)
        num["BestLexQfourU"] = f"{S.mean(best_u, 'Q4u'):.3f}"
        num["BestLexQfourUName"] = LABELS.get(best_u, esc(best_u))

    # ---------------- validity without human queries (scripts/real_noise_stats.py, validity_analyses.py)
    tw = {"Q1": "Qone", "Q2": "Qtwo", "Q3": "Qthree", "Q4": "Qfour", "Q5": "Qfive", "Q4u": "QfourU", "Q5u": "QfiveU",
          "Q3u": "QthreeU", "Q4s": "QfourS", "Q5g": "QfiveG"}
    pct = lambda x: None if x is None else f"{100 * x:.0f}"                      # noqa: E731
    rn = (load_json("real_noise.json") or {}).get("transcriptions")
    if rn:
        k = rn["kinds"]
        num.update({"RealPairs": rn["pairs"], "RealAligned": f"{rn['aligned_syllables']:,}",
                    "RealSubs": rn["substitutions"], "RealSubsPerK": f"{rn['per_1000_aligned']:.1f}",
                    "RealOtherWord": k.get("other_word", 0),
                    "RealOtherWordShare": pct(k.get("other_word", 0) / max(1, rn["substitutions"])),
                    "RealOrthographic": k.get("orthographic", 0), "RealNotVietnamese": k.get("not_vietnamese", 0),
                    "RealChanN": sum(rn["channel_counts"].values()),
                    "RealKnownInitialShare": pct(rn["known_initial_share"])})
        for op in ("initial", "final", "tone", "typo"):
            num[f"RealChan{op.capitalize()}"] = pct(rn["channel_shares"][op])
            num[f"DefW{op.capitalize()}"] = pct(rn["default_weights"][op])
        ci = rn.get("kind_shares_ci95", {})
        if "per_1000_aligned" in ci:
            num["RealSubsPerKLo"], num["RealSubsPerKHi"] = (f"{x:.1f}" for x in ci["per_1000_aligned"])
        for side, tag in (("cross_source", "Cross"), ("same_source", "Same")):
            d = rn.get(side)
            if d:
                num[f"Real{tag}Pairs"], num[f"Real{tag}PerK"] = d["pairs"], f"{d['per_1000_aligned']:.1f}"
                for kk, v in (d.get("kind_shares") or {}).items():
                    num[f"Real{tag}{KIND_KEY[kk]}"] = pct(v)
        # realised kinds of each sweep channel, classified like the observed substitutions
        realised_kinds = sweep_kinds(sweep)
        num["SweepSubsN"] = sum(realised_kinds.get("N", {}).values())
        rows = []
        for kk, label in KIND_LABEL.items():
            n_obs = k.get(kk, 0)
            share = n_obs / max(1, rn["substitutions"])
            lo, hi = ci.get(kk, (None, None))
            num[f"Obs{KIND_KEY[kk]}"] = pct(share)
            if lo is not None:
                num[f"Obs{KIND_KEY[kk]}Lo"], num[f"Obs{KIND_KEY[kk]}Hi"] = pct(lo), pct(hi)
            cells = [str(n_obs), f"{100 * share:.0f}" + (f" [{100 * lo:.0f}, {100 * hi:.0f}]" if lo is not None else "")]
            for ch in "NEW":
                tot_ch = sum(realised_kinds.get(ch, {}).values())
                if tot_ch:
                    v = realised_kinds[ch].get(kk, 0) / tot_ch
                    num[f"Sweep{ch}{KIND_KEY[kk]}"] = pct(v)
                    cells.append(f"{100 * v:.0f}")
                else:
                    cells.append("--")
            rows.append(f"{label} & " + " & ".join(cells) + " \\\\")
        other = {ch: sum(v for kk, v in realised_kinds.get(ch, {}).items() if kk not in KIND_LABEL) for ch in "NEW"}
        if any(other.values()):
            rows.append("Spelling variant only & -- & -- & " + " & ".join(
                f"{100 * other[ch] / max(1, sum(realised_kinds.get(ch, {}).values())):.0f}" for ch in "NEW") + " \\\\")
        open(os.path.join(out, "tab_channel.tex"), "w").write(f"""\\begin{{table}}[t]
\\centering\\small
\\caption{{Kinds of substituted syllables. Observed: {rn['substitutions']} substitutions in {rn['aligned_syllables']:,}
aligned syllables of {rn['pairs']} pairs of transcriptions of the same song, with 95\\% bootstrap intervals over pairs.
N, E, W: realised shares in the noise sweep, classified the same way (all rates pooled). N draws four hand-set
operations; E draws the same operations with the observed shares; W samples the observed kinds and substitution
pairs.}}
\\label{{tab:channel}}
\\setlength{{\\tabcolsep}}{{4pt}}
\\begin{{tabular}}{{@{{}}l r l rrr@{{}}}}
\\toprule
 & \\multicolumn{{2}}{{c}}{{observed}} & \\multicolumn{{3}}{{c}}{{sweep (\\%)}} \\\\
\\cmidrule(lr){{2-3}} \\cmidrule(l){{4-6}}
Kind & $n$ & \\% [95\\% CI] & N & E & W \\\\
\\midrule
{chr(10).join(rows)}
\\bottomrule
\\end{{tabular}}
\\end{{table}}
""")
    va = load_json("validity.json")
    if va:
        num["ValSystems"] = len(va["systems"])
        num["TauFamily"] = f"{va['family_agreement']['tau']:.2f}"
        loo = va["leave_one_out"]
        lo = min(loo, key=lambda t: loo[t]["tau"])
        for t_, v_ in loo.items():
            num[f"TauLoo{tw.get(t_, t_)}"] = f"{v_['tau']:.2f}".replace("-", "\\ensuremath{-}")
        num.update({"TauLooMin": f"{loo[lo]['tau']:.2f}", "TauLooMinType": lo,
                    "TauLooMax": f"{max(v['tau'] for v in loo.values()):.2f}"})
        pairs = {kk: v for kk, v in va["type_agreement"].items() if all(x in TYPES for x in kk.split("~"))}
        if pairs:
            pm = min(pairs, key=lambda kk: pairs[kk]["tau"])
            num.update({"TauPairMin": f"{pairs[pm]['tau']:.2f}", "TauPairMinTypes": pm.replace("~", "/"),
                        "TauPairMedian": f"{float(np.median([v['tau'] for v in pairs.values()])):.2f}"})
        ns = va.get("noise_stability", {})
        if "N50" in ns:
            num["TauNoiseFifty"] = f"{ns['N50']['tau']:.2f}"
            num["NoiseSystems"] = len(va.get("noise_systems", []))
        # how much the estimated channel changes MRR@10 compared with the hand-set one, over the same systems
        diffs = []
        for sname in va.get("noise_systems", []):
            for r_ in (10, 20, 30, 40, 50):
                e_, n_ = S.mean(sname, f"E{r_}"), S.mean(sname, f"N{r_}")
                if e_ is not None and n_ is not None:
                    diffs.append(e_ - n_)
        if diffs:
            num["ChanDiffMean"] = f"{float(np.mean(diffs)):+.3f}".replace("-", "\\ensuremath{-}").replace("+", "\\ensuremath{+}")
            num["ChanDiffMaxAbs"] = f"{max(abs(d) for d in diffs):.3f}"
            num["ChanDiffShareSmall"] = f"{100 * np.mean([abs(d) <= 0.02 for d in diffs]):.0f}"
        ch = va.get("channel_agreement", {})
        if ch:
            num["TauChannelMin"] = f"{min(v['tau'] for v in ch.values()):.2f}"
            num["TauChannelMean"] = f"{float(np.mean([v['tau'] for v in ch.values()])):.2f}"
        dp = va["discriminative_power"]
        ta = va["type_agreement"]

        def tau_pair(x, y):
            v = ta.get(f"{x}~{y}") or ta.get(f"{y}~{x}")
            return None if v is None else v["tau"]
        rows = []
        for t in [x for x in TYPES + UTYPES + VARIANTS if x in dp]:
            d = dp[t]
            num[f"DP{tw.get(t, t)}"] = pct(d["share"])
            if t in loo:
                tau_t = f"{loo[t]['tau']:.2f}"
            else:
                tp = tau_pair(t, PARENT[t])
                tau_t = "--" if tp is None else f"{tp:.2f} ({PARENT[t]})"
                if tp is not None:
                    num[f"TauParent{tw.get(t, t)}"] = f"{tp:.2f}".replace("-", "\\ensuremath{-}")
            rows.append(f"{t} & {d['queries']} & {100 * d['share']:.0f} & {tau_t} \\\\")
            if t == TYPES[-1]:
                rows.append("\\midrule")
        main_dp = [dp[t]["share"] for t in TYPES if t in dp]
        num["DPMin"], num["DPMax"] = pct(min(main_dp)), pct(max(main_dp))
        num["DPPairs"] = dp[TYPES[0]]["pairs"] if TYPES[0] in dp else None
        ms = va.get("mixture_sensitivity")
        if ms:
            num["MixDraws"] = ms["draws"]
            num["MixTauMean"], num["MixTauPfive"] = f"{ms['tau_mean']:.2f}", f"{ms['tau_p05']:.2f}"
            top = ms["equal_weight_first"]
            num["MixFirstStays"] = pct(ms["first_place"].get(top, 0) / ms["draws"])
            num["MixFirstName"] = hybrid_label(top) if top.startswith("hybrid_") else LABELS.get(top, esc(top))
            num["MixNFirst"] = len(ms["first_place"])
        num["NDupRuns"] = len(va.get("duplicates", {}))
        open(os.path.join(out, "tab_validity.tex"), "w").write(f"""\\begin{{table}}[t]
\\centering\\small
\\caption{{Sensitivity of each query type over {len(va['systems'])} systems: discriminative power (share of the
{num['DPPairs']} system pairs whose MRR@10 differs at $\\alpha=0.05$, two-sided randomization test) and Kendall's
$\\tau$ between the system ordering on the type and on the macro of the other main types, or, for the variants
below the line, on the type it varies (in brackets).}}
\\label{{tab:validity}}
\\begin{{tabular}}{{lrrl}}
\\toprule
Type & Queries & Disc.\\ power (\\%) & $\\tau$ \\\\
\\midrule
{chr(10).join(rows)}
\\bottomrule
\\end{{tabular}}
\\end{{table}}
""")
        # the systems compared, for the appendix (duplicates listed with the run they repeat)
        dup = va.get("duplicates", {})
        names_ = ", ".join(f"\\texttt{{{esc(x)}}}" for x in va["systems"])
        dtxt = "; ".join(f"\\texttt{{{esc(x)}}} = \\texttt{{{esc(y)}}}" for x, y in sorted(dup.items()))
        open(os.path.join(out, "systems_list.tex"), "w").write(
            f"{{\\footnotesize\\raggedright {names_}.\\par}}\n" +
            (f"\n\\noindent Left out as identical on every test query: {{\\footnotesize {dtxt}.}}\n" if dup else ""))

    # ---------------- further analyses (scripts/extra_analyses.py)
    signed = lambda x: "0.000" if abs(x) < 5e-4 else \
        f"{x:+.3f}".replace("-", "\\ensuremath{-}").replace("+", "\\ensuremath{+}")                 # noqa: E731
    ex = load_json("extra_analyses.json")
    ckey = dict(key_of, N50="NFifty")
    pl = ex.get("planned", {})
    ckey.update({"W50": "WFifty", "Q3u": "ThreeU", "Q4s": "FourS", "Q5g": "FiveG"})
    if pl:
        cols = TYPES + ["N50", "W50"]
        rows = []
        for key, r in pl.items():
            tag = "".join(part.capitalize() for part in key.split("_"))
            cells = []
            for c in TYPES + ["Q4u", "Q5u"] + VARIANTS + ["N50", "W50"]:
                v = r["cols"].get(c)
                if not v:
                    if c in cols:
                        cells.append("--")
                    continue
                if c in cols:
                    cells.append(signed(v["delta"]) + ("$^{*}$" if v.get("p_holm", 1) < 0.05 else ""))
                num[f"Pl{tag}{ckey[c]}"] = signed(v["delta"])
                num[f"Pl{tag}{ckey[c]}A"] = f"{v['a']:.3f}"
                num[f"Pl{tag}{ckey[c]}B"] = f"{v['b']:.3f}"
                num[f"Pl{tag}{ckey[c]}Sig"] = "yes" if v.get("p_holm", 1) < 0.05 else "no"
            md, ci = r.get("macro_delta"), r.get("macro_delta_ci95")
            if md is None:
                cells.append("--")
            else:
                num[f"Pl{tag}Macro"] = signed(md)
                txt = signed(md)
                if ci:
                    lo_, hi_ = (signed(x) for x in ci)
                    num[f"Pl{tag}MacroLo"], num[f"Pl{tag}MacroHi"] = lo_, hi_
                    txt += f" [{lo_}, {hi_}]"
                cells.append(txt)
            short_lab = {"restore_lex": "Unmarked bigrams $\\rightarrow$ restored marked bigrams",
                         "lexfusion": "Restored bigrams $\\rightarrow$ lexical RRF",
                         "ql": "BM25 $\\rightarrow$ query likelihood (syllables)",
                         "word": "Syllables $\\rightarrow$ words (BM25)",
                         "phon": "Syllable bigrams $\\rightarrow$ sound-alike key",
                         "charx": "Unmarked bigrams $\\rightarrow$ char.\\ 5-grams across syllables",
                         "adaptive": "Hybrid: one weight vector $\\rightarrow$ per predicted type",
                         "restore": "mE5-base: queries as typed $\\rightarrow$ restored",
                         "naft": "mE5-small, restored: zero-shot $\\rightarrow$ NA-FT",
                         "nonoise": "NA-FT (seed 1) $\\rightarrow$ without noise",
                         "randneg": "NA-FT (seed 1) $\\rightarrow$ random negatives",
                         "hybrid": "Lexical RRF $\\rightarrow$ selected hybrid",
                         "oracle": "Hybrid: predicted $\\rightarrow$ true query type",
                         "otherdense": "Hybrid: other dense model",
                         "rerank": "Hybrid $\\rightarrow$ cross-encoder reranking"}.get(key, r["label"])
            rows.append(f"{short_lab} & " + " & ".join(cells) + " \\\\")
        n_rr = min((v["n"] for v in pl.get("rerank", {}).get("cols", {}).values()), default=None)
        open(os.path.join(out, "tab_planned.tex"), "w").write(f"""\\begin{{table*}}[t]
\\centering
\\caption{{Planned comparisons on the test split: change in MRR@10 from system A to system B (B $-$ A), per query
type and at 50\\% noise under the hand-set (N50) and the observed (W50) channel. $^{{*}}$: $p<0.05$, two-sided paired randomization test with 10{{,}}000 permutations, Holm
correction over the {len(pl)} comparisons within each column. Systems with three seeds enter with their mean per
query. Macro: change of the macro average over Q1--Q5 with a 95\\% bootstrap interval (queries resampled within
each type).{f' Reranking was run on {n_rr} sampled queries per type.' if n_rr else ''}}}
\\label{{tab:planned}}
\\footnotesize
\\setlength{{\\tabcolsep}}{{4pt}}
\\begin{{tabular}}{{@{{}}l rrrrr rr l@{{}}}}
\\toprule
Comparison (A $\\rightarrow$ B) & Q1 & Q2 & Q3 & Q4 & Q5 & N50 & W50 & Macro [95\\% CI] \\\\
\\midrule
{chr(10).join(rows)}
\\bottomrule
\\end{{tabular}}
\\end{{table*}}
""")
    dw = ex.get("dense_weight")
    if dw:
        wtag = WEIGHT_TAG
        rows = []
        for w, v in dw["by_weight"].items():
            t = v["test"]
            rows.append(f"{float(w):g} & " + " & ".join(f3(t[x]) for x in TYPES + ["Q4u"]) + f" & {f3(t['macro'])} \\\\")
            for x in TYPES + ["Q4u", "macro"]:
                num[f"Dw{wtag[float(w)]}{key_of[x]}"] = f"{t[x]:.3f}"
            num[f"Dw{wtag[float(w)]}DevMacro"] = f"{v['dev']['macro']:.3f}"
        num["DwMacroTunedW"] = f"{dw['macro_tuned_weight']:g}"
        sel = dw.get("system", SEL or "hybrid_naft_e5small_s3_rest_adaptive")
        hp = fp.get(sel[:-len("_adaptive")], {})
        ad = sel
        if hp and ad in ranks:
            on = sorted({f"{v['weights'][-1]:g}" for t, v in hp["per_type"].items() if v["weights"][-1] > 0})
            on_types = [t for t, v in hp["per_type"].items() if v["weights"][-1] > 0]
            num["DwAdaptiveWeight"] = "/".join(on)
            num["DwAdaptiveTypes"] = " and ".join(on_types)
            rows.append("\\midrule")
            rows.append(f"per predicted type & " + " & ".join(f3(SN.mean(ad, x)) for x in TYPES + ["Q4u"])
                        + f" & {f3(SN.macro(ad))} \\\\")
        open(os.path.join(out, "tab_dense_weight.tex"), "w").write(f"""\\begin{{table}}[t]
\\centering
\\caption{{One weight $w$ for the dense list for every query: hybrid of the lexical lists (weights fixed at their tuned
values) and {dense_label(sel[len('hybrid_'):-len('_adaptive')])}, MRR@10 on the test split. The $w$ that maximises the macro average on the
development split is {dw['macro_tuned_weight']:g}. Last row: $w={num.get('DwAdaptiveWeight', '?')}$ for queries predicted
as {num.get('DwAdaptiveTypes', '?')}, $w=0$ otherwise.}}
\\label{{tab:dense_weight}}
\\small
\\setlength{{\\tabcolsep}}{{4.5pt}}
\\begin{{tabular}}{{@{{}}l rrrrrr r@{{}}}}
\\toprule
$w$ & Q1 & Q2 & Q3 & Q4 & Q5 & Q4u & Macro \\\\
\\midrule
{chr(10).join(rows)}
\\bottomrule
\\end{{tabular}}
\\end{{table}}
""")
    cat = ex.get("catalogue")
    if cat:
        fr = cat["fractions"]
        ftag = {0.25: "Quarter", 0.5: "Half", 1.0: "Full"}
        names = {"bm25_syl": "BM25, syllables", "bm25_nodiac12": "BM25, unmarked bigrams",
                 "restoreall_bm25_syl12": "Restore + bigrams", "lexall_global": "Lexical RRF",
                 "dense_e5base_rest": "mE5-base, restored",
                 "dense_naft_e5small_s1_rest": "NA-FT (seed 1), restored",
                 "hybrid_e5base_rest_adaptive": "Hybrid with mE5-base",
                 "hybrid_naft_e5small_s3_rest_adaptive": "Selected hybrid"}
        tkey = {"bm25_syl": "Syl", "bm25_nodiac12": "NodiacBi", "restoreall_bm25_syl12": "RestAllBi",
                "lexall_global": "FuseGlobal", "dense_e5base_rest": "DenseEbaseRest",
                "dense_naft_e5small_s1_rest": "NaftSoneRest", "hybrid_e5base_rest_adaptive": "HybEbaseAdaptive",
                "hybrid_naft_e5small_s3_rest_adaptive": "HybNaftAdaptive"}
        show = ["Q1", "Q4", "Q5", "macro"]
        rows = []
        for sname, res in cat["systems"].items():
            cells = []
            for x in show:
                for f in fr:
                    cells.append(f3(res[str(f)][x]))
                    num[f"Cat{tkey.get(sname, letters(sname))}{key_of[x]}{ftag.get(f, str(f))}"] = f"{res[str(f)][x]:.3f}"
            rows.append(f"{names.get(sname, esc(sname))} & " + " & ".join(cells) + " \\\\")
        # loss per doubling of the catalogue, from the smallest share to the full catalogue
        lo, hi = min(fr), max(fr)
        for sname, res in cat["systems"].items():
            for x in TYPES + ["macro"]:
                d = (res[str(lo)][x] - res[str(hi)][x]) / math.log2(hi / lo)
                num[f"Cat{tkey.get(sname, letters(sname))}{key_of[x]}Drop"] = f"{d:.3f}"
        num["CatClusters"] = cat["clusters"]
        num["CatReps"] = cat["reps"]
        fh = " & ".join(f"{int(100 * f)}\\%" for f in fr)
        spans = " ".join(f"\\cmidrule(lr){{{2 + i * len(fr)}-{1 + (i + 1) * len(fr)}}}" for i in range(len(show)))
        open(os.path.join(out, "tab_catalogue.tex"), "w").write(f"""\\begin{{table*}}[t]
\\centering
\\caption{{MRR@10 on the test split when the index keeps a random share of the {cat['clusters']} clusters (the target
cluster is always kept; mean over {cat['reps']} random draws). Rankings are those of the full index restricted to the
kept songs; the hybrid is fused again from the restricted lists.}}
\\label{{tab:catalogue}}
\\scriptsize
\\setlength{{\\tabcolsep}}{{3.2pt}}
\\begin{{tabular}}{{@{{}}l {' '.join(['r' * len(fr)] * len(show))}@{{}}}}
\\toprule
 & {' & '.join(f'\\multicolumn{{{len(fr)}}}{{c}}{{{x if x != "macro" else "Macro"}}}' for x in show)} \\\\
{spans}
Share kept & {' & '.join([fh] * len(show))} \\\\
\\midrule
{chr(10).join(rows)}
\\bottomrule
\\end{{tabular}}
\\end{{table*}}
""")
    er = ex.get("errors")
    if er:
        pc = lambda x: f"{100 * x:.0f}"                                             # noqa: E731
        for t, v in er.items():
            k = "Err" + tw.get(t, t)
            num[f"{k}N"], num[f"{k}Fail"] = v["n"], v["failures"]
            for pt, c in v["failures_by_predicted_type"].items():
                num[f"{k}Pred{tw.get(pt, pt)}"] = c
            for side, tag in (("fail", "Fail"), ("ok", "Ok")):
                d = v[side]
                if d["overlap"] is None:
                    continue
                num[f"{k}Overlap{tag}"] = f"{d['overlap']:.2f}"
                num[f"{k}Run{tag}"] = f"{d['run']:.1f}"
                num[f"{k}Len{tag}"] = f"{d['len']:.1f}"
                num[f"{k}Medley{tag}"] = pc(d["medley_first"])
                num[f"{k}Artist{tag}"] = pc(d["names_artist"])
                num[f"{k}SameArtist{tag}"] = pc(d["same_artist_above"])
            if v["failures"]:
                num[f"{k}ArtistFailN"] = round(v["fail"]["names_artist"] * v["failures"])
                num[f"{k}SameArtistFailN"] = round(v["fail"]["same_artist_above"] * v["failures"])
                num[f"{k}MedleyFailN"] = round(v["fail"]["medley_first"] * v["failures"])

    # ---------------- first-revision analyses (scripts/round1_analyses.py) and the query variants
    thou = lambda n: f"{n:,}".replace(",", "{,}")                                    # noqa: E731
    r1 = load_json("round1_analyses.json")
    am = r1.get("ambiguity")
    if am:
        num["AmbigChecked"], num["AmbigRejected"] = thou(am["fragments_checked"]), thou(am["rejected"])
        num["AmbigRejectedShare"] = f"{100 * am['rejected_share']:.1f}"
    sf = r1.get("short_fragments")
    if sf:
        for n_, w_ in (("3", "Three"), ("4", "Four"), ("5", "Five"), ("8", "Eight"), ("12", "Twelve")):
            if n_ in sf:
                num[f"ShortShare{w_}"] = f"{100 * sf[n_]['share']:.0f}" if sf[n_]["share"] >= 0.1 else \
                    f"{100 * sf[n_]['share']:.1f}"
        num["ShortFragments"] = thou(sf["3"]["fragments"]) if "3" in sf else None
    rc = r1.get("restorer_cap")
    if rc:
        num["RestCap"], num["RestCapCoverage"] = rc["cap"], f"{100 * rc['token_coverage']:.1f}"
        num["RestCapTypesOver"], num["RestUnmarkedTypes"] = rc["types_over_cap"], thou(rc["unmarked_types"])
        num["RestFormsPerType"], num["RestFormsPerToken"] = f"{rc['forms_per_type']:.1f}", f"{rc['forms_per_token']:.1f}"
    cc = r1.get("cluster_check")
    if cc:
        q1_, q2_, q3_ = cc["ratio_quartiles"]
        num.update({"CluPairs": cc["pairs"], "CluRatioQOne": f"{q1_:.2f}", "CluRatioMedian": f"{q2_:.2f}",
                    "CluRatioQThree": f"{q3_:.2f}", "CluShareBelowHalf": f"{100 * cc['share_below_0.5']:.0f}"})
    vs = load_json("variants_stats.json")
    for t, v in vs.items():
        tg = tw.get(t, t)
        if "written" in v:
            num[f"Var{tg}Written"], num[f"Var{tg}Kept"] = v["written"], v["kept"]
            num[f"Var{tg}Rejected"] = sum(v.get("rejected", {}).values())
        if "telex" in v:
            num[f"Var{tg}Telex"] = v["telex"]
    au = load_json("cluster_audit.json")
    if au:
        num["AuditLowPairs"], num["AuditLowSame"] = au["within_low_similarity"]["pairs"], au["within_low_similarity"]["same_song"]
        num["AuditMissedCand"] = au["missed_merge_candidates"]["pairs"]
    if "qtype_logreg" in lp and lp["qtype_logreg"].get("dev_accuracy_out_of_fold") is not None:
        num["QtypeDevAccOOF"] = f"{100 * lp['qtype_logreg']['dev_accuracy_out_of_fold']:.1f}"
    if "rm3" in lp:
        num["RMthreeDocs"], num["RMthreeTerms"] = lp["rm3"]["fb_docs"], lp["rm3"]["fb_terms"]
        num["RMthreeOrigW"] = f"{lp['rm3']['orig_weight']:g}"
    TYPED_ = TYPES + UTYPES
    show7 = TYPES + UTYPES

    def row7(lab, d, dev=None, bold=False):
        cells = [f3(d.get(t)) for t in show7] + [f3(d.get("macro"))]
        if dev is not None:
            cells.append(f3(dev))
        return f"{lab} & " + " & ".join(cells) + " \\\\"
    fa = r1.get("fusion_alt")
    if fa:
        rows = [row7(f"Selected hybrid: RRF of five lists, weights per predicted type (ref.)",
                     fa["reference"]["test"], fa["reference"]["dev"]["macro7"])]
        num["FaRefMacro"] = f"{fa['reference']['test']['macro']:.3f}"
        for nm, lab, vlab in (("rrf2", "RRF of lexical RRF and the dense list", "$w$"),
                              ("convex", "Convex score fusion (min--max normalised)", "$a$")):
            d = fa[nm]
            pt = sorted({f"{v:g}" for v in d["per_type_value"].values()})
            rows.append(row7(f"{lab}, one {vlab}$={d['global_value']:g}$", d["global"]["test"], d["global"]["dev"]["macro7"]))
            rows.append(row7(f"\\quad {vlab} per predicted type ({', '.join(pt)})", d["adaptive"]["test"],
                             d["adaptive"]["dev"]["macro7"]))
            tg = {"rrf2": "Rrf", "convex": "Convex"}[nm]
            for v_, k_ in (("global", "Global"), ("adaptive", "Adaptive")):
                num[f"Fa{tg}{k_}Macro"] = f"{d[v_]['test']['macro']:.3f}"
                num[f"Fa{tg}{k_}Four"] = f"{d[v_]['test']['Q4']:.3f}"
            num[f"Fa{tg}Value"] = f"{d['global_value']:g}"
        ro = fa["route"]
        ch_ = ", ".join(f"{t}: {v.replace('hybrid_global', 'hybrid').replace('lexical', 'lex.')}"
                        for t, v in sorted(ro["choice"].items()) if t in TYPES + UTYPES)
        rows.append(row7("Routing to lexical, dense or hybrid by predicted type", ro["test"], ro["dev"]["macro7"]))
        num["FaRouteMacro"], num["FaRouteFour"] = f"{ro['test']['macro']:.3f}", f"{ro['test']['Q4']:.3f}"
        num["FaRouteChoice"] = ch_
        mg = fa["margin"]
        rows.append(row7(f"Margin router without labels ($\\theta={mg['theta']:.2f}$, $w={mg['weight']:g}$)", mg["test"],
                         mg["dev"]["macro7"]))
        num["FaMarginMacro"], num["FaMarginFour"] = f"{mg['test']['macro']:.3f}", f"{mg['test']['Q4']:.3f}"
        num["FaMarginTheta"], num["FaMarginW"] = f"{mg['theta']:.2f}", f"{mg['weight']:g}"
        sr = mg.get("share_routed_test", {})
        if sr:
            num["FaMarginRoutedFour"], num["FaMarginRoutedOne"] = pct(sr.get("Q4", 0)), pct(sr.get("Q1", 0))
        hdr = " & ".join(show7)
        open(os.path.join(out, "tab_fusion_alt.tex"), "w").write(f"""\\begin{{table*}}[t]
\\centering
\\caption{{Ways of adding the dense list to the lexical lists, each tuned on the development split (MRR@10 on the
test split; last column: dev macro over the seven dev types, the tuning criterion). Per-type values are kept only
where they beat the single value in 5-fold cross-validation on dev. The margin router needs no query type: the
dense list joins with weight $w$ when the relative score margin between the first two songs of the restored
bigram run is below $\\theta$.}}
\\label{{tab:fusion_alt}}
\\footnotesize
\\setlength{{\\tabcolsep}}{{3.6pt}}
\\begin{{tabular}}{{@{{}}l rrrrrrr r r@{{}}}}
\\toprule
System & {hdr} & Macro & Dev \\\\
\\midrule
{chr(10).join(rows)}
\\bottomrule
\\end{{tabular}}
\\end{{table*}}
""")
    qa = r1.get("qtype_ablation")
    if qa:
        for nm, tg in (("full", "Full"), ("no_length", "NoLength")):
            num[f"QtAbl{tg}Acc"] = f"{100 * qa[nm]['accuracy']['all']:.1f}"
            num[f"QtAbl{tg}RecallFour"] = f"{100 * qa[nm]['accuracy']['Q4']:.0f}"
            num[f"QtAbl{tg}Macro"] = f"{qa[nm]['hybrid']['macro']:.3f}"
        lb = qa["full"].get("leading_bai")
        if lb:
            num["LeadBaiQueries"], num["LeadBaiN"] = lb["queries"], lb["with_lead"]
            num["LeadBaiShare"] = pct(lb["with_lead"] / max(1, lb["queries"]))
            if lb["accuracy_as_written"] is not None:
                num["LeadBaiAccWritten"] = f"{100 * lb['accuracy_as_written']:.0f}"
                num["LeadBaiAccStripped"] = f"{100 * lb['accuracy_without_lead']:.0f}"
            for t, cnt in lb.get("variants_predicted", {}).items():
                tot_ = sum(cnt.values()) or 1
                for pt_, c_ in cnt.items():
                    num[f"VarPred{tw.get(t, t)}As{tw.get(pt_, pt_)}"] = pct(c_ / tot_)
    npl = r1.get("nonpilot")
    if npl:
        num["PilotClusters"], num["NonPilotQueries"] = npl["pilot_clusters"], thou(npl["nonpilot_queries"])
        num["NonPilotShare"] = pct(npl["nonpilot_queries"] / max(1, npl["test_queries"]))
        nlab = {"bm25_syl": "BM25, syllables", "restoreall_bm25_syl12": "Restore + uni+bigrams",
                "lexall_global": "Lexical RRF, weights tuned on dev", "lexall_adaptive": "Lexical RRF, per predicted type"}
        rows = []
        for nm, v in npl["systems"].items():
            lab = nlab.get(nm) or (("Selected hybrid" if nm == SEL else hybrid_label(nm)) if nm.startswith("hybrid_")
                                   else "Dense " + dense_label(nm[len("dense_"):]))
            a_, n_ = v["all"], v["nonpilot"]
            rows.append(f"{lab} & " + " & ".join(f3(n_.get(t)) for t in TYPES) + f" & {f3(n_['macro'])} & {f3(a_['macro'])} \\\\")
            k_ = {"bm25_syl": "Syl", "restoreall_bm25_syl12": "RestAllBi", "lexall_global": "FuseGlobal",
                  "lexall_adaptive": "FuseAdaptive"}.get(nm, "Sel" if nm == SEL else letters(nm))
            num[f"NonPilot{k_}Macro"], num[f"All{k_}Macro"] = f"{n_['macro']:.3f}", f"{a_['macro']:.3f}"
        nby = npl["by_type"]
        open(os.path.join(out, "tab_nonpilot.tex"), "w").write(f"""\\begin{{table}}[t]
\\centering
\\caption{{Test queries about the {thou(npl['nonpilot_queries'])} of {thou(npl['test_queries'])} test queries whose
cluster lies outside the {npl['pilot_clusters']} clusters of the pilot catalogue, on which the method was developed
(MRR@10; Q1--Q5: {', '.join(str(nby.get(t, 0)) for t in TYPES)} queries). Last column: macro on all test queries.}}
\\label{{tab:nonpilot}}
\\footnotesize
\\setlength{{\\tabcolsep}}{{3.5pt}}
\\begin{{tabular}}{{@{{}}l rrrrr r r@{{}}}}
\\toprule
 & \\multicolumn{{6}}{{c}}{{outside the pilot clusters}} & all \\\\
\\cmidrule(lr){{2-7}}
System & Q1 & Q2 & Q3 & Q4 & Q5 & Macro & Macro \\\\
\\midrule
{chr(10).join(rows)}
\\bottomrule
\\end{{tabular}}
\\end{{table}}
""")
    rk = r1.get("rerank")
    if rk:
        smp = rk["sampled"]
        num["RerankSampleTest"] = max(smp["test"].values()) if smp.get("test") else None
        num["RerankSampleDev"] = max(smp["dev"].values()) if smp.get("dev") else None
        num["RerankAlpha"] = f"{rk['alpha']:g}"
        st_ = [t for t, v in rk["selective_types"].items() if v]
        num["RerankSelTypes"] = ", ".join(st_) if st_ else "none"
        rows = []
        for nm, lab in (("first_stage", "Selected hybrid (first stage)"), ("reranked", "Cross-encoder order"),
                        ("interpolated", f"Interpolated, $\\alpha={rk['alpha']:g}$"),
                        ("selective", f"Selective (predicted {num['RerankSelTypes']})")):
            d = rk[nm]["test"]
            cells = [f3(d.get(t)) for t in show7] + [f3(d.get("macro7"))]
            rows.append(f"{lab} & " + " & ".join(cells) + f" & {f3(rk[nm]['dev'].get('macro7'))} \\\\")
            tg = "".join(x.capitalize() for x in nm.split("_"))
            num[f"Rr{tg}Macro"] = f"{d['macro7']:.3f}"
            for t in show7:
                if d.get(t) is not None:
                    num[f"Rr{tg}{key_of[t]}"] = f"{d[t]:.3f}"
        open(os.path.join(out, "tab_rerank.tex"), "w").write(f"""\\begin{{table*}}[t]
\\centering
\\caption{{Cross-encoder reranking of the top {num.get('RerankDepth', 20)} of the selected hybrid on a stratified sample
({num['RerankSampleTest']} test and {num['RerankSampleDev']} dev queries per type; MRR@10, macro over the seven
types). Interpolation mixes the min--max normalised cross-encoder and first-stage scores with weight $\\alpha$;
the selective variant reranks only the predicted types for which reranking helped on the dev sample; both are
tuned on the dev sample (last column).}}
\\label{{tab:rerank}}
\\footnotesize
\\setlength{{\\tabcolsep}}{{4pt}}
\\begin{{tabular}}{{@{{}}l rrrrrrr r r@{{}}}}
\\toprule
 & {' & '.join(show7)} & Macro & Dev \\\\
\\midrule
{chr(10).join(rows)}
\\bottomrule
\\end{{tabular}}
\\end{{table*}}
""")
    rt = r1.get("restore_train")
    if rt:
        num["HybRestTrainMacro"] = f"{rt['test']['macro']:.3f}"
        num["HybRestTrainDense"] = "yes" if len(rt["swap"]) > 1 else "no"
        for t in TYPES:
            num[f"HybRestTrain{key_of[t]}"] = f"{rt['test'][t]:.3f}"
    # the variants against the types they vary, paired by song (Q3u by fragment)
    var_systems = [("bm25_syl", "BM25, syllables"), ("bm25_nodiac12", "BM25, unmarked uni+bigrams"),
                   ("bm25_char5x", "BM25, char.\\ 5-grams across syllables"),
                   ("restoreall_bm25_syl12", "Restore + uni+bigrams"), ("bm25_meta", "BM25, metadata only"),
                   ("lexall_global", "Lexical RRF")]
    if zero_runs:
        var_systems.append((best_zero, dense_label(best_zero[len('dense_'):]).replace(" on restored queries", ", restored")))
    if f"naftmean_e5small{REST_SUFFIX}" in ranks:
        var_systems.append((f"naftmean_e5small{REST_SUFFIX}", "NA-FT mE5-small, restored"))
    if SEL:
        var_systems.append((SEL, "Selected hybrid"))
    vpairs = {}
    for t in VARIANTS:
        par = PARENT[t]
        parent_ids = set(groups.get(par, []))
        vpairs[t] = [(qv, qv.replace(f"-{t}-", f"-{par}-")) for qv in groups.get(t, [])
                     if qv.replace(f"-{t}-", f"-{par}-") in parent_ids]
    if any(vpairs.values()):
        pvals_v, delta_v = defaultdict(dict), defaultdict(dict)
        for t, prs in vpairs.items():
            for sname, _ in var_systems:
                if not SN.has(sname) or not prs:
                    continue
                prs_ = [(x, y) for x, y in prs if x in ranks[sname] and y in ranks[sname]]
                if not prs_:
                    continue
                va_ = SN.vec(sname, t, "rr10", [x for x, _ in prs_])
                vb_ = SN.vec(sname, PARENT[t], "rr10", [y for _, y in prs_])
                pvals_v[t][sname] = randomization_test(va_, vb_)
                delta_v[t][sname] = float(va_.mean() - vb_.mean())
        adj_v = {t: holm(pv) for t, pv in pvals_v.items()}
        rows = []
        for sname, lab in var_systems:
            if not SN.has(sname):
                continue
            cells = []
            for t in VARIANTS:
                prs = [(x, y) for x, y in vpairs[t] if x in ranks[sname] and y in ranks[sname]]
                if not prs:
                    cells += ["--", "--"]
                    continue
                pa_ = float(SN.vec(sname, PARENT[t], "rr10", [y for _, y in prs]).mean())
                vv_ = float(SN.vec(sname, t, "rr10", [x for x, _ in prs]).mean())
                mk = ""
                if adj_v.get(t, {}).get(sname, 1) < 0.05:
                    mk = "$^{\\ddagger}$" if delta_v[t][sname] < 0 else "$^{\\dagger}$"
                cells += [f3(pa_), f3(vv_) + mk]
                kk = {"bm25_syl": "Syl", "bm25_nodiac12": "NodiacBi", "bm25_char5x": "CharFiveX",
                      "restoreall_bm25_syl12": "RestAllBi", "bm25_meta": "Meta", "lexall_global": "FuseGlobal"}.get(
                    sname, "Sel" if sname == SEL else ("NaftRest" if sname.startswith("naftmean") else "BestZero"))
                num[f"Var{kk}{key_of[t]}"] = f"{vv_:.3f}"
                num[f"Var{kk}{key_of[t]}Parent"] = f"{pa_:.3f}"
            rows.append(f"{lab} & " + " & ".join(cells) + " \\\\")
        for t in VARIANTS:
            num[f"NPairs{key_of[t]}"] = len(vpairs[t])
        open(os.path.join(out, "tab_variants.tex"), "w").write(f"""\\begin{{table*}}[t]
\\centering
\\caption{{Test-only variants against the queries they vary, on the same songs (MRR@10): noisy fragments typed
without marks (Q3u, {len(vpairs['Q3u'])} pairs), paraphrases written by a second prompt that asks for a short, loose
recollection (Q4s, {len(vpairs['Q4s'])} pairs), and descriptions written from a gist of the lyrics by a writer that
saw neither the lyrics nor the title or artist (Q5g, {len(vpairs['Q5g'])} pairs). $\\ddagger$/$\\dagger$: the variant
is significantly harder/easier for that system (paired randomization test, Holm correction within each column,
$p<0.05$).}}
\\label{{tab:variants}}
\\footnotesize
\\setlength{{\\tabcolsep}}{{4pt}}
\\begin{{tabular}}{{@{{}}l rr rr rr@{{}}}}
\\toprule
System & Q3 & Q3u & Q4 & Q4s & Q5 & Q5g \\\\
\\midrule
{chr(10).join(rows)}
\\bottomrule
\\end{{tabular}}
\\end{{table*}}
""")
    # 95% bootstrap intervals of the macro (queries resampled within each type) for the main systems
    rng_b = np.random.default_rng(0)
    for sname, kk in (("bm25_syl", "Syl"), ("restoreall_bm25_syl12", "RestAllBi"), ("lexall_global", "FuseGlobal"),
                      (SEL, "Sel")):
        if not sname or not SN.has(sname):
            continue
        vecs = [SN.vec(sname, t, "rr10") for t in TYPES if groups.get(t)]
        boots = [np.mean([v[rng_b.integers(0, len(v), len(v))].mean() for v in vecs]) for _ in range(2000)]
        num[f"CI{kk}Lo"], num[f"CI{kk}Hi"] = f"{np.percentile(boots, 2.5):.3f}", f"{np.percentile(boots, 97.5):.3f}"
        half = [1.96 * v.std(ddof=1) / math.sqrt(len(v)) for v in vecs]
        num[f"CIHalf{kk}Max"] = f"{max(half):.3f}"
    if SEL and SN.has(SEL):
        num["SelMacro"] = f"{SN.macro(SEL):.3f}"
        for t in TYPES + UTYPES + VARIANTS + ["N50", "W50"]:
            v = SN.mean(SEL, t)
            if v is not None:
                num[f"Sel{ckey.get(t, key_of.get(t, t))}"] = f"{v:.3f}"

    # ---------------- cost table
    nd, lx_ = eff.get("dense", {}).get("naft_e5small_s1"), eff.get("lexical", {})
    if nd and lx_ and "restore_all" in lp:
        num["EffHybridMs"] = f"{nd['ms_per_query_encode'] + nd['ms_per_query_search_single'] + sum(v['ms_per_query'] for v in lx_.values()) + lp['restore_all']['ms_per_query']:.0f}"
    if eff.get("dense"):
        rows = []
        dn = {"e5small": "mE5-small", "naft_e5small_s1": "NA-FT mE5-small", "e5base": "mE5-base",
              "bkai": "bkai bi-encoder"}
        for n in ("e5small", "naft_e5small_s1", "e5base", "bkai"):
            row = eff["dense"].get(n)
            if row:
                rows.append(f"{dn[n]} & {row['passages_per_s']:.0f} & {row['full_encode_minutes']:.0f} min & "
                            f"{row['index_mb']:.0f} & {row['ms_per_query_encode']:.0f} & "
                            f"{row['ms_per_query_search_single']:.0f} \\\\")
        lx = eff.get("lexical", {})
        lrows = []
        for an, lab in (("syl12", "BM25, syllable uni+bigrams"), ("nodiac12", "BM25, unmarked uni+bigrams")):
            if an in lx:
                lrows.append(f"{lab} & -- & {lx[an]['index_s']:.0f} s & -- & -- & {lx[an]['ms_per_query']:.1f} \\\\")
        if "restore_all" in lp:
            lrows.append(f"Diacritic restoration & -- & -- & -- & {lp['restore_all']['ms_per_query']:.1f} & -- \\\\")
        if rr:
            lrows.append(f"bge-reranker-v2-m3, top {num['RerankDepth']} & -- & -- & -- & -- & "
                         f"{1000 * float(num['RerankSecPerQuery']):,.0f} \\\\")
        open(os.path.join(out, "tab_efficiency.tex"), "w").write(f"""\\begin{{table}}[t]
\\centering
\\caption{{Cost on four CPU cores without a GPU. Index: encoding the {eff['dense'][next(iter(eff['dense']))]['index_passages']:,}
passages (dense) or building the inverted index (lexical), and the size of the passage vectors. Query: encoding one
query; search: exact search for one query (ms). mE5-small and NA-FT mE5-small share one architecture; their difference
shows the variation between timing runs.}}
\\label{{tab:efficiency}}
\\small
\\setlength{{\\tabcolsep}}{{4pt}}
\\begin{{tabular}}{{@{{}}l r r r r r@{{}}}}
\\toprule
 & Passages/s & Index & MB & Query (ms) & Search (ms) \\\\
\\midrule
{chr(10).join(rows)}
\\midrule
{chr(10).join(lrows)}
\\bottomrule
\\end{{tabular}}
\\end{{table}}
""")

    # ---------------- summary for reports
    summary = {s: {t: S.mean(s, t) for t in TYPES + SWEEP} | {"macro": S.macro(s), "dev_macro": D.macro(s)}
               for s in ranks if not s.startswith("naftmean_")}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)

    for k, v in num.items():          # a minus sign, not a hyphen, in running text
        if k.startswith("Tau") and isinstance(v, str) and v.startswith("-"):
            num[k] = "\\ensuremath{-}" + v[1:]
    with open(os.path.join(out, "numbers.tex"), "w") as fh:
        fh.write("% generated by scripts/make_paper_tables.py; do not edit\n")
        for k, v in sorted(num.items()):
            if v is None:
                v = "??"
            fh.write(f"\\newcommand{{\\{k}}}{{{v}}}\n")
    print(f"wrote {out}: {len(num)} numbers, {len(ranks)} systems")


def plot_noise(S, systems, path, channels=("N",)):
    """MRR@10 against the nominal noise rate, one panel per channel (N: hand-set, W: observed)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    # categorical slots in fixed order (validated palette), plus a marker per series for print
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
    markers = ["o", "s", "^", "D", "v", "P"]
    plt.rcParams.update({"font.family": "serif", "font.size": 9, "axes.edgecolor": "#8a8984",
                         "axes.linewidth": 0.6, "xtick.color": "#52514e", "ytick.color": "#52514e"})
    titles = {"N": "hand-set channel (N)", "W": "observed channel (W)", "E": "re-weighted channel (E)"}
    fig, axes = plt.subplots(1, len(channels), figsize=(3.3 * len(channels), 3.0), sharey=True, squeeze=False)
    xs = [0, 10, 20, 30, 40, 50]
    shown = [x for x in systems if S.has(x[0])]
    for ax, ch in zip(axes[0], channels):
        for i, (s, lab) in enumerate(shown):
            ys = [S.mean(s, "N0" if x == 0 else f"{ch}{x}") for x in xs]
            ax.plot(xs, ys, color=colors[i % len(colors)], marker=markers[i % len(markers)], markersize=4.5,
                    linewidth=1.4, linestyle="--" if lab == "Selected hybrid" else "-", label=lab,
                    markeredgecolor="white", markeredgewidth=0.8, solid_capstyle="round", solid_joinstyle="round")
        ax.set_title(titles.get(ch, ch), fontsize=8.5, color="#0b0b0b")
        ax.set_xlabel("corrupted syllables, nominal (%)", color="#0b0b0b")
        ax.set_xticks(xs)
        ax.set_ylim(0, 1.02)
        ax.grid(axis="y", color="#e6e5e0", linewidth=0.6)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    axes[0][0].set_ylabel("MRR@10", color="#0b0b0b")
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=7, loc="lower center", ncol=3 if len(channels) > 1 else 2,
               bbox_to_anchor=(0.5, -0.02), handlelength=2.2, columnspacing=1.2)
    fig.tight_layout(pad=0.3, rect=(0, 0.13 if len(channels) > 1 else 0.2, 1, 1))
    fig.savefig(path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


if __name__ == "__main__":
    main()
