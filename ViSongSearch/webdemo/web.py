"""Máy chủ web FastAPI của demo. Giao diện tìm kiếm chỉnh từ typesense/showcase-songs-search: thay bộ
chuyển Typesense bằng một searchClient gọi API của chính demo, trả kết quả đúng định dạng InstantSearch."""
from __future__ import annotations

import copy
import html
import json
import math
import os
import random
import re
import time
import uuid
from urllib.parse import parse_qs, quote

from starlette.concurrency import run_in_threadpool
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from visong.noise import corrupt
from visong.queries import fragment
from visong.text import clean, remove_diacritics

from . import charts, clustering, seo, temporal
from .core import MODELS, RUN_NAMES, TYPE_NAMES
from .coverage import COVERAGE
from .coverage import counts as coverage_counts
from .dialog import DialogManager
from .extraction import lyric_relations, split_artists
from .langmodel import self_attention
from .sentiment import features as sentiment_features
from .lexicons import HERE
from .spelling import edit_distance
from .store import Store

BASE_URL = os.environ.get("VISONG_BASE_URL", "http://127.0.0.1:8000")
# dòng ghi chú của trang hợp âm còn sót trong lời (không đưa vào đoạn trích)
NOISE_LINE = re.compile(r"(vòng hòa thanh|vòng hoà thanh|hợp âm|capo|^\s*(tone|intro|outro|điệu|nhịp)\s*:)", re.I)


def create_app(engine, evaluator, runtime: str) -> FastAPI:
    app = FastAPI(title="ViSongSearch demo", docs_url="/api/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
    tpl = Jinja2Templates(directory=os.path.join(HERE, "templates"))
    tpl.env.globals.update(models=MODELS, type_names=TYPE_NAMES, summary=engine.summary, base_url=BASE_URL,
                           slug=seo.slug, line_chart=charts.line_chart, bar_chart=charts.bar_chart)
    tpl.env.filters["pct"] = lambda x: f"{100 * x:.1f}%" if isinstance(x, (int, float)) else "–"
    tpl.env.filters["f3"] = lambda x: f"{x:.3f}" if isinstance(x, (int, float)) else "–"
    store = Store(os.path.join(runtime, "demo.sqlite3"))
    memo = {}

    def compression():
        if "comp" not in memo:
            memo["comp"] = engine.pindex.compression_stats()
        return memo["comp"]

    dialog = DialogManager(engine)
    dialog.llm.warm_up()          # nạp sẵn mô hình Ollama (nếu có) để câu đầu ở chế độ LLM không phải chờ
    examples = make_examples(engine)

    def session_of(request: Request) -> str:
        return request.cookies.get("vss") or uuid.uuid4().hex[:12]

    def page(request, name, ctx, status=200):
        resp = tpl.TemplateResponse(request, name, {"request": request, **ctx}, status_code=status)
        if not request.cookies.get("vss"):
            resp.set_cookie("vss", session_of(request), max_age=30 * 24 * 3600, samesite="lax")
        return resp

    # ------------------------------------------------------------ trang tìm kiếm
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return page(request, "search.html", {"examples": examples, "weights_source": engine.weights_source})

    @app.post("/api/1/indexes/{index}/queries")
    async def multi_search(request: Request):
        body = await request.json()
        sess = session_of(request)
        results = []
        for i, req in enumerate(body.get("requests", [])):
            params = req.get("params", {})
            if isinstance(params, str):
                params = {k: (json.loads(v[0]) if v and v[0][:1] in "[{" else v[0]) for k, v in parse_qs(params).items()}
            index = req.get("indexName", "songs")
            model = index.split("/")[-1] if "/" in index else "fusion"
            model = model if model in MODELS else "fusion"
            query = params.get("query", "") or ""
            hpp = int(params.get("hitsPerPage", 12))
            facets = params.get("facets", [])
            if isinstance(facets, str):
                facets = [facets]
            res = engine.search(query, model, int(params.get("page", 0)), hpp, params.get("facetFilters"), facets,
                                int(params.get("maxValuesPerFacet", 10)),
                                params.get("highlightPreTag", "__ais-highlight__"),
                                params.get("highlightPostTag", "__/ais-highlight__"), seed=hash(sess) % 1000)
            qid = uuid.uuid4().hex[:12]
            main = hpp > 0 and query.strip() and i == 0
            if main:
                store.log_query(qid, sess, query.strip(), model, res["analysis"].get("type"), res["nbHits"], res["ms"],
                                {"teams": res["trace"].get("teams")} if model == "interleave" else None)
            for h in res["hits"]:
                h["click"] = f"/go?q={qid}&d={h['objectID']}&r={h['rank']}&t={h.get('team', '')}"
            out = {k: res[k] for k in ("hits", "nbHits", "page", "nbPages", "hitsPerPage", "processingTimeMS", "facets",
                                         "exhaustiveFacetsCount", "query")}
            out.update({"index": index, "params": "", "exhaustiveNbHits": True})
            if main:
                out["userData"] = [explain(engine, res, model)]
            results.append(out)
        return JSONResponse({"results": results})

    @app.post("/api/sffv")
    async def sffv(request: Request):
        body = await request.json()
        out = []
        for req in body.get("requests", []):
            p = req.get("params", {})
            vals = engine.facet_values(p.get("facetName", ""), p.get("facetQuery", ""), int(p.get("maxFacetHits", 10)))
            out.append({"facetHits": vals, "exhaustiveFacetsCount": True, "processingTimeMS": 1})
        return JSONResponse(out)

    @app.get("/api/suggest")
    def suggest(q: str = ""):
        return {"suggestions": store.suggestions(q, [s.title for s in engine.songs])}

    @app.get("/go")
    def go(request: Request, q: str = "", d: str = "", r: int = 0, t: str = ""):
        if d in engine.by_id:
            store.log_click(q, session_of(request), d, r, t)
            return RedirectResponse(f"/bai-hat/{d}-{seo.slug(engine.by_id[d].title)}", status_code=302)
        return RedirectResponse("/", status_code=302)

    # ------------------------------------------------------------ trang bài hát (SEO)
    def song_ctx(d: str, light: bool = False) -> dict:
        s = engine.by_id[d]
        lines = s.lines()
        excerpt = [l for l in lines if not NOISE_LINE.search(l)][:8]
        url = seo.song_url(BASE_URL, s)
        mood = engine.mood[d]
        kw = engine.vsm.keywords(engine.pos[d], 8)
        ctx = {"s": s, "d": d, "excerpt": excerpt, "n_lines": len(lines), "url": url, "page_title": seo.page_title(s),
               "artists": split_artists(s.artist) or [s.artist], "desc": seo.meta_description(s, " ".join(excerpt)),
               "mood": mood, "keywords": kw, "jsonld": seo.json_ld(s, url, mood["label"], kw),
               "time_tags": engine.time_tags[d], "topic": engine.topic[d], "light": light,
               "pagerank_pct": round(100 * engine.pr_pct[d]), "inlinks": engine.inlinks[d]}
        if light:
            return ctx
        ctx.update({
            "emotion": engine.emotion[d], "timeline": temporal.song_timeline(s.lyrics),
            "events": engine.events.summary(s.lyrics), "relations": lyric_relations(s.lyrics, 8),
            "entities": [e for e in engine.gazetteer.find(s.lyrics, strict=True) if e["type"] != "THỜI_GIAN"][:10],
            "similar": [(engine.by_id[x], sc) for x, sc in engine.similar(d, 6)],
            "coclick": [(engine.by_id[x], c) for x, c in store.co_clicked(d) if x in engine.by_id],
            "versions": [engine.by_id[x] for x in engine.members[engine.cluster_of[d]] if x != d][:6],
            "tags": store.tags_of(d), "auto_tags": auto_tags(engine, d),
            "nb": nb_lines(lines),
        })
        return ctx

    def nb_lines(lines) -> dict:
        """Naive Bayes trên từng dòng (cả bài là một văn bản dài thì xác suất luôn dồn về 0 hoặc 1)."""
        c = {}
        for l in lines:
            f = [w for w in sentiment_features(l) if w in engine.nb.vocab]
            if f:
                lab = engine.nb.predict(f)
                c[lab] = c.get(lab, 0) + 1
        tot = sum(c.values()) or 1
        return {k: v / tot for k, v in sorted(c.items())}

    @app.get("/bai-hat", response_class=HTMLResponse)
    def any_song():
        d = max(engine.ids, key=lambda x: engine.pagerank[x])
        return RedirectResponse(f"/bai-hat/{d}-{seo.slug(engine.by_id[d].title)}", status_code=302)

    @app.get("/bai-hat/{slug}", response_class=HTMLResponse)
    def song_page(request: Request, slug: str):
        t0 = time.perf_counter()
        d = slug.split("-", 1)[0]
        if d not in engine.by_id:
            return page(request, "message.html", {"title": "Không tìm thấy", "text": "Không có bài hát này."}, 404)
        s = engine.by_id[d]
        ctx = song_ctx(d)
        canonical = f"{d}-{seo.slug(s.title)}"
        if slug != canonical:                     # một URL chuẩn cho mỗi bài (SEO: tránh trùng nội dung)
            return RedirectResponse(f"/bai-hat/{canonical}", status_code=301)
        resp = page(request, "song.html", ctx)
        ms = (time.perf_counter() - t0) * 1000
        body = resp.body.decode("utf-8")
        audit = seo.audit(body, s, ctx["url"], engine.inlinks[d], max(1, 100 - ctx["pagerank_pct"]), ms)
        body = body.replace("<!--SEO_AUDIT-->", tpl.get_template("_audit.html").render(audit=audit))
        out = HTMLResponse(body)
        if not request.cookies.get("vss"):
            out.set_cookie("vss", session_of(request), max_age=30 * 24 * 3600, samesite="lax")
        return out

    @app.post("/api/tag")
    async def add_tag(request: Request):
        # đọc form bằng tay để không cần cài thêm python-multipart
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        doc_id = (form.get("doc_id") or [""])[0]
        tag = (form.get("tag") or [""])[0]
        if doc_id in engine.by_id and tag.strip():
            store.add_tag(doc_id, tag, session_of(request))
        return RedirectResponse(f"/bai-hat/{doc_id}-{seo.slug(engine.by_id.get(doc_id).title if doc_id in engine.by_id else '')}#the",
                                status_code=303)

    @app.get("/the", response_class=HTMLResponse)
    def tags_page(request: Request, t: str = ""):
        songs = [(engine.by_id[d], c) for d, c in store.docs_with_tag(t) if d in engine.by_id] if t else []
        inferred = []
        if t:
            tagged = {s.doc_id for s, _ in songs}
            score = {}
            for s, c in songs:
                for x, sim in engine.similar(s.doc_id, 8):
                    if x not in tagged:
                        score[x] = score.get(x, 0) + sim * c
            inferred = [(engine.by_id[x], round(v, 3)) for x, v in sorted(score.items(), key=lambda kv: -kv[1])[:8]]
        auto = {}
        for d in engine.ids:
            for tg in auto_tags(engine, d):
                auto[tg] = auto.get(tg, 0) + 1
        auto_cloud = sorted(auto.items(), key=lambda kv: -kv[1])[:40]
        return page(request, "tags.html", {"cloud": store.tag_cloud(), "t": t, "songs": songs, "inferred": inferred,
                                           "auto_cloud": auto_cloud})

    # ------------------------------------------------------------ trợ lý
    @app.get("/tro-ly", response_class=HTMLResponse)
    def chat_page(request: Request):
        return page(request, "chat.html", {})

    @app.post("/api/chat")
    async def chat(request: Request):
        body = await request.json()
        return await run_in_threadpool(dialog.turn, session_of(request) + ":" + body.get("conv", ""),
                                       body.get("text", ""), "llm" if body.get("mode") == "llm" else "rule")

    # ------------------------------------------------------------ đánh giá và thống kê
    @app.get("/danh-gia", response_class=HTMLResponse)
    def eval_page(request: Request):
        ev = evaluator.state
        noise_x = [int(round(100 * r["rate"])) for r in ev.get("noise_sweep") or []]
        return page(request, "eval.html", {"ev": ev, "eng": engine, "noise_x": noise_x})

    @app.get("/api/eval")
    def eval_json():
        return evaluator.state

    @app.get("/thong-ke", response_class=HTMLResponse)
    def stats_page(request: Request):
        rng = random.Random(1)
        sample = rng.sample(engine.ids, min(25, len(engine.ids)))
        audits = []
        for d in sample:
            s = engine.by_id[d]
            t0 = time.perf_counter()
            ctx = song_ctx(d, light=True)
            html_page = tpl.get_template("song.html").render(request=request, **ctx)
            ms = (time.perf_counter() - t0) * 1000
            a = seo.audit(html_page, s, ctx["url"], engine.inlinks[d], max(1, 100 - ctx["pagerank_pct"]), ms)
            audits.append((s, a))
        checks = {}
        for _, a in audits:
            for c in a["checks"]:
                checks.setdefault(c["name"], [0, 0])
                checks[c["name"]][0] += c["ok"]
                checks[c["name"]][1] += 1
        g = engine.graph
        return page(request, "stats.html", {
            "an": store.analytics(), "wins": store.interleave_wins(), "audits": audits, "checks": checks,
            "top_pr": g.top(12, "pagerank"), "top_auth": g.top(8, "authority"), "top_hub": g.top(8, "hub"),
            "gstats": g.stats(), "comms": engine.communities, "rel": engine.relation_counts,
            "comp": compression(), "eng": engine, "timing": engine.timing, "examples": examples,
            "conv": [(i + 1, round(math.log10(max(x, 1e-12)), 3)) for i, x in enumerate(g.pr_trace[:40])]})

    # ------------------------------------------------------------ phòng lab
    @app.get("/phong-lab", response_class=HTMLResponse)
    def lab(request: Request):
        return page(request, "lab.html", {"examples": examples})

    @app.get("/api/lab/{tool}")
    def lab_api(tool: str, q: str = "", a: str = "", b: str = "", w: str = ""):
        q = q.strip()[:300]
        e = engine
        if tool == "edit":
            r = edit_distance(clean(a)[:30], clean(b)[:30], sub_cost=2 if w == "2" else 1, transpose=w != "2")
            return {"distance": r["distance"], "matrix": r["matrix"], "ops": r["ops"], "a": r["a"], "b": r["b"]}
        if tool == "spell":
            return {"candidates": [{"tok": t, "cands": e.speller.candidates(t)[:8]} for t in clean(q).split()[:12]],
                    "nbest": e.speller.decode(q, 5)}
        if tool == "restore":
            from visong.nbest import nbest as nb
            out = {"nbest": [{"text": t, "logp": round(lp, 2)} for t, lp in nb(e.restorer, q, 5)],
                   "table": [{"tok": t, "cands": [[w2, c] for w2, c in e.restorer.by_plain.get(t, {}).most_common(6)]}
                             for t in remove_diacritics(clean(q)).split()[:12]]}
            orders = {}
            for o in (1, 2, 3):               # bản sao nông: không đổi bộ phục hồi dùng chung
                r = copy.copy(e.restorer)
                r.order = o
                orders[o] = r.restore(q)
            out["orders"] = orders
            return out
        if tool == "lm":
            return {"pp": [e.lmtools.perplexity(q, o) for o in (1, 2, 3)], "next": e.lmtools.next_words(q, 10),
                    "generate": [e.lmtools.generate(q, 10, seed=i) for i in range(3)],
                    "shuffled": e.lmtools.perplexity(" ".join(random.Random(0).sample(clean(q).split(), len(clean(q).split()))), 3)["perplexity"] if q else None}
        if tool == "mask":
            return e.lmtools.fill_mask(q, 10)
        if tool == "embed":
            word = clean(w or q)
            return {"word": word, "nearest": e.emb.nearest(word, 12), "stats": e.emb.stats,
                    "analogy": e.emb.analogy(*clean(a).split()[:3], k=5) if len(clean(a).split()) >= 3 else []}
        if tool == "attn":
            return self_attention(q, e.emb)
        if tool == "parse":
            u = e.grammar.understand(q, kb=e)
            return {k: u.get(k) for k in ("chunks", "tree", "bracket", "logprob", "alternatives", "chart", "deps",
                                          "transitions", "logic", "slots", "model_check")}
        if tool == "time":
            return {"tags": temporal.tag(q), "lines": [{"line": l, "tense": temporal.tense_of(l)} for l in q.split("\n")]}
        if tool == "sentiment":
            from .sentiment import features
            return {"lexicon": e.sent.score_line(q), "emotions": e.sent.emotions(q), "connotations": e.sent.connotations(q),
                    "nb": e.nb.explain(q), "nb_top": e.nb.top_features(10), "nb_stats": e.nb_stats}
        if tool == "wsd":
            info = e.wordnet.analyze(q)
            rel = {x["sense"]: e.wordnet.relations(x["sense"]) for x in info if x["sense"]}
            return {"words": info, "relations": rel, "expansion": e.wordnet.expansion(q)[0]}
        if tool == "ie":
            return {"entities": e.gazetteer.find(q), "relations": lyric_relations(q, 10), "events": e.events.extract(q, 12)}
        if tool == "assoc":
            return e.assoc.related(w or q, 10)
        if tool == "index":
            word = remove_diacritics(clean(w or q)).split()
            pv = e.pindex.posting_view(word[0]) if word else {}
            inter = e.pindex.intersect(word[0], word[1]) if len(word) >= 2 else None
            inter_noskip = e.pindex.intersect(word[0], word[1], skips=False) if len(word) >= 2 else None
            return {"posting": pv, "intersect": inter, "intersect_noskip": inter_noskip}
        if tool == "daat":
            return {"daat": e.pindex.daat(q, 10), "taat": e.pindex.taat(q, 10)}
        if tool == "boolean":
            r = e.pindex.boolean(q)
            return {"explain": r["explain"], "n": len(r["docs"]),
                    "docs": [{"id": e.ids[d], "title": e.by_id[e.ids[d]].title} for d in r["docs"][:15]]}
        if tool == "qtype":
            an = e.analyze(q)
            return {"type": an["type"], "proba": an["type_proba"], "source": an.get("type_source")}
        if tool == "cluster":
            return cluster_demo(e)
        return JSONResponse({"error": "không có công cụ này"}, status_code=404)

    # ------------------------------------------------------------ đối chiếu môn học, SEO kỹ thuật
    @app.get("/doi-chieu", response_class=HTMLResponse)
    def coverage(request: Request):
        return page(request, "coverage.html", {"cov": COVERAGE, "counts": coverage_counts()})

    @app.get("/sitemap.xml")
    def sitemap():
        return Response(seo.sitemap(BASE_URL, engine.songs, time.strftime("%Y-%m-%d")), media_type="application/xml")

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        return seo.robots(BASE_URL)

    return app


def auto_tags(engine, d: str) -> list[str]:
    tags = []
    m = engine.mood[d]["label"]
    if m != "trung tính":
        tags.append(m)
    tags += engine.time_tags[d][:2]
    emo = engine.emotion[d]
    if emo:
        tags.append(max(emo, key=emo.get))
    return list(dict.fromkeys(tags))


def explain(engine, res, model) -> dict:
    a = res["analysis"]
    t = res["trace"]
    runs = []
    for n, info in (t.get("runs") or {}).items():
        runs.append({"name": RUN_NAMES.get(n, n), "key": n, "weight": info["weight"],
                     "top": [engine.by_id[x].title for x in info["top"][:3]]})
    return {"type": a.get("type"), "type_name": TYPE_NAMES.get(a.get("type"), ""), "type_proba": a.get("type_proba"),
            "type_source": a.get("type_source"), "restorations": a.get("restorations", [])[:3],
            "spell": a.get("spell"), "slots": {k: v for k, v in (a.get("slots") or {}).items() if v and k != "artist_text"},
            "logic": (a.get("grammar") or {}).get("logic", {}).get("formula"),
            "bracket": (a.get("grammar") or {}).get("bracket"), "wsd": a.get("wsd", []), "expansion": a.get("expansion"),
            "timex": [x["text"] + " → " + x["value"] for x in a.get("timex", [])],
            "entities": [f"{x['type']}: {x['text']}" for x in a.get("entities", []) if x["type"] != "THỜI_GIAN"],
            "sentiment": (a.get("sentiment") or {}).get("label"), "runs": runs, "boosts": t.get("boosts"),
            "model": MODELS.get(model, model), "ms": res["ms"], "ms_analyze": a.get("ms_analyze"),
            "weights_source": engine.weights_source, "content": a.get("content")}


def make_examples(engine) -> dict:
    """Ví dụ truy vấn sinh từ chính kho của bạn, mỗi loại một câu."""
    rng = random.Random(4)
    top = sorted(engine.ids, key=lambda d: -engine.pagerank[d])[:60]
    ex = {}
    for d in top:
        s = engine.by_id[d]
        f = fragment(s, rng, 6, 9)
        if not f:
            continue
        ex = {"Q1": f[0], "Q2": remove_diacritics(f[0]), "Q3": corrupt(f[0], 0.3, rng)[0], "song": s.title}
        break
    artist = engine.artist_count.most_common(1)[0][0] if engine.artist_count else ""
    title_song = engine.by_id[top[1]] if len(top) > 1 else None
    ex["Q4"] = "bài hát kể về một người con xa quê nhớ mẹ"
    ex["Q5"] = "bài hát buồn về mùa thu"
    ex["artist"] = f"bài hát của {artist} có câu " + (" ".join(clean(engine.by_id[next(d for d in engine.ids if artist in engine.by_id[d].artist)].lines()[0]).split()[:4]) if artist else "")
    ex["title"] = title_song.title if title_song else ""
    ex["boolean"] = '"mùa thu" AND nhớ -mưa'
    ex["artist_name"] = clean(artist)
    return ex


def cluster_demo(e) -> dict:
    import numpy as np
    rng = np.random.default_rng(2)
    idx = rng.choice(len(e.ids), 60, replace=False)
    X = e.vsm.Z[idx]
    km = clustering.kmeans_manual(X, 3, seed=0)
    small = idx[:12]
    names = [e.by_id[e.ids[i]].title[:28] for i in small]
    ag = clustering.agglomerative_steps(e.vsm.Z[small], names, 11)
    i, j = int(idx[0]), int(idx[1])
    from visong.text import syllables
    dist = clustering.distances(e.vsm.Z[i], e.vsm.Z[j], set(syllables(e.songs[i].lyrics)), set(syllables(e.songs[j].lyrics)))
    db = clustering.dbscan_roles(X, eps=0.45, min_samples=3)
    return {"kmeans": km["history"], "agglomerative": ag["steps"], "pair": [e.songs[i].title, e.songs[j].title],
            "distances": dist, "dbscan": {"n_clusters": db["n_clusters"], "n_noise": db["n_noise"],
                                          "roles": dict(__import__("collections").Counter(db["roles"]))},
            "k_choice": e.k_choice}
