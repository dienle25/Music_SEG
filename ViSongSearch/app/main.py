"""ViSongSearch web demo (SEG301): search page, result snippets, song pages with
schema.org JSON-LD, sitemap.xml, robots.txt, and a query/click log with a small
analytics dashboard.

    VISONG_WORK=work uvicorn app.main:app --port 8000
    # optional dense list: VISONG_DENSE=e5base VISONG_DENSE_MODEL=models/multilingual-e5-base
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from visong.corpus import composer_of, singers_of

from .engine import Engine

WORK = os.environ.get("VISONG_WORK", "work")
LOG_DB = os.environ.get("VISONG_LOG", os.path.join(WORK, "search_log.sqlite"))
BASE_URL = os.environ.get("VISONG_BASE_URL", "http://localhost:8000")

app = FastAPI(title="ViSongSearch")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
engine = Engine(WORK, os.environ.get("VISONG_DENSE"), os.environ.get("VISONG_DENSE_MODEL"))
_lock = threading.Lock()


def db():
    con = sqlite3.connect(LOG_DB, check_same_thread=False)
    con.executescript("""
    create table if not exists queries(id text primary key, ts real, session text, query text,
        restored text, qtype text, n_results integer, ms real);
    create table if not exists clicks(query_id text, ts real, doc_id text, rank integer);
    """)
    return con


CON = db()


def session_of(request: Request) -> str:
    return request.cookies.get("vss") or uuid.uuid4().hex[:12]


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    top = CON.execute("select query, count(*) c from queries where n_results > 0 group by query "
                      "order by c desc limit 8").fetchall()
    resp = templates.TemplateResponse(request, "index.html", {"top": top, "n_songs": len(engine.songs)})
    resp.set_cookie("vss", session_of(request), max_age=30 * 24 * 3600)
    return resp


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    res = engine.search(q)
    qid = uuid.uuid4().hex[:16]
    with _lock:
        CON.execute("insert into queries values(?,?,?,?,?,?,?,?)",
                    (qid, time.time(), session_of(request), q, res["restored"], res["qtype"],
                     len(res["results"]), res["ms"]))
        CON.commit()
    return templates.TemplateResponse(request, "results.html", {"res": res, "qid": qid, "q": q})


@app.get("/api/search")
def api_search(q: str = "", k: int = 10):
    return engine.search(q, k)


@app.get("/go")
def go(query_id: str, doc: str, rank: int):
    with _lock:
        CON.execute("insert into clicks values(?,?,?,?)", (query_id, time.time(), doc, rank))
        CON.commit()
    return RedirectResponse(f"/song/{doc}", status_code=302)


@app.get("/song/{doc_id}", response_class=HTMLResponse)
def song(request: Request, doc_id: str):
    s = engine.songs.get(doc_id)
    if s is None:
        return HTMLResponse("Không tìm thấy bài hát", status_code=404)
    lines = s.lines()
    excerpt = lines[:8]                       # short excerpt only: lyrics are copyrighted
    ld = {"@context": "https://schema.org", "@type": "MusicRecording", "name": s.title,
          "inLanguage": "vi", "url": f"{BASE_URL}/song/{doc_id}", "sameAs": s.url}
    if composer_of(s):
        ld["recordingOf"] = {"@type": "MusicComposition", "name": s.title,
                             "composer": {"@type": "Person", "name": composer_of(s)}}
    if singers_of(s):
        ld["byArtist"] = [{"@type": "Person", "name": n} for n in singers_of(s)[:5]]
    if s.genre:
        ld["genre"] = s.genre
    return templates.TemplateResponse(request, "song.html", {
        "s": s, "excerpt": excerpt, "jsonld": json.dumps(ld, ensure_ascii=False),
        "description": " / ".join(excerpt[:2])[:155]})


@app.get("/sitemap.xml")
def sitemap():
    urls = "".join(f"<url><loc>{BASE_URL}/song/{d}</loc></url>" for d in engine.songs)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return Response(xml, media_type="application/xml")


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return f"User-agent: *\nDisallow: /search\nDisallow: /go\nSitemap: {BASE_URL}/sitemap.xml\n"


@app.get("/stats", response_class=HTMLResponse)
def stats(request: Request):
    n, ms, zero = CON.execute("select count(*), avg(ms), sum(n_results = 0) from queries").fetchone()
    clicks = CON.execute("select count(distinct query_id), avg(1.0 / rank), avg(rank = 1) from clicks").fetchone()
    by_type = CON.execute("select qtype, count(*) from queries group by qtype order by 2 desc").fetchall()
    top = CON.execute("select query, count(*) from queries group by query order by 2 desc limit 15").fetchall()
    zero_q = CON.execute("select query, count(*) from queries where n_results = 0 group by query "
                         "order by 2 desc limit 10").fetchall()
    sessions = CON.execute("select count(distinct session) from queries").fetchone()[0]
    return templates.TemplateResponse(request, "stats.html", {
        "n": n or 0, "ms": ms or 0, "zero": zero or 0, "clicked": clicks[0] or 0,
        "mrr_click": clicks[1] or 0, "ctr1": clicks[2] or 0, "by_type": by_type, "top": top,
        "zero_q": zero_q, "sessions": sessions, "load_s": engine.load_seconds})
