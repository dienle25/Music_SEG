"""Tối ưu cho máy tìm kiếm (SEG 8 Search Engine Optimization).

On-page: thẻ title, meta description, một thẻ H1, URL dễ đọc (slug), từ khóa trong title/H1,
dữ liệu có cấu trúc schema.org MusicRecording (JSON-LD), canonical, Open Graph.
Technical: sitemap.xml, robots.txt, thời gian phản hồi, kích thước trang.
Off-page: số liên kết trỏ vào và PageRank lấy từ đồ thị liên kết đã thu thập.
Đo lường: CTR theo vị trí trong nhật ký truy vấn (store.py).
"""
from __future__ import annotations

import html
import json
import re

from visong.text import clean, remove_diacritics


def slug(text: str) -> str:
    s = remove_diacritics(clean(text)).replace(" ", "-")
    return re.sub(r"-+", "-", s).strip("-")[:60] or "bai-hat"


def song_url(base: str, song) -> str:
    return f"{base}/bai-hat/{song.doc_id}-{slug(song.title)}"


def page_title(song) -> str:
    """Thẻ title 30-65 ký tự, có tên bài ở đầu."""
    t, a = song.title.strip(), (song.artist or "").strip()
    for cand in (f"{t} - {a} | Lời bài hát", f"Lời bài hát {t} - {a}", f"{t} - {a}", f"{t} | Lời bài hát - ViSongSearch",
                 f"Lời bài hát {t} | ViSongSearch"):
        if 30 <= len(cand) <= 65:
            return cand
    return (f"{t} - {a}")[:62].rstrip() + "..." if len(f"{t} - {a}") > 65 else f"{t} - {a} | Lời bài hát"


def meta_description(song, excerpt: str) -> str:
    first = " ".join(excerpt.split())[:110]
    text = f"Lời bài hát {song.title} - {song.artist}: {first}"
    return text[:157] + "..." if len(text) > 160 else text


def json_ld(song, url: str, mood: str, keywords: list[str]) -> str:
    data = {
        "@context": "https://schema.org", "@type": "MusicRecording", "name": song.title, "url": url,
        "byArtist": [{"@type": "MusicGroup", "name": n.strip()} for n in (song.artist or "").split(",") if n.strip()],
        "inAlbum": {"@type": "MusicAlbum", "name": song.extra.get("album")} if song.extra.get("album") not in (None, "", "None") else None,
        "genre": song.genre or None, "keywords": ", ".join(keywords) or None,
        "isBasedOn": song.url, "inLanguage": "vi", "about": mood,
    }
    return json.dumps({k: v for k, v in data.items() if v}, ensure_ascii=False, indent=2)


def audit(page_html: str, song, url: str, inlinks: int, pagerank_pct: float, ms: float) -> dict:
    """Chấm các tiêu chí SEO của một trang bài hát. Mỗi tiêu chí: đạt / chưa đạt và giải thích."""
    title = re.search(r"<title>(.*?)</title>", page_html, re.S)
    title = html.unescape(title.group(1).strip()) if title else ""
    desc = re.search(r'<meta name="description" content="([^"]*)"', page_html)
    desc = html.unescape(desc.group(1)) if desc else ""
    h1 = re.findall(r"<h1[^>]*>(.*?)</h1>", page_html, re.S)
    imgs = re.findall(r"<img [^>]*>", page_html)
    no_alt = [i for i in imgs if " alt=" not in i]
    internal = len(re.findall(r'href="/(?!static)', page_html))
    kw = clean(song.title)
    checks = [
        ("Title 30-65 ký tự", 30 <= len(title) <= 65, f"{len(title)} ký tự: {title}"),
        ("Meta description 70-160 ký tự", 70 <= len(desc) <= 160, f"{len(desc)} ký tự"),
        ("Đúng một thẻ H1", len(h1) == 1, f"{len(h1)} thẻ H1"),
        ("Tên bài có trong title", kw in clean(title), ""),
        ("Tên bài có trong H1", any(kw in clean(re.sub('<.*?>', '', h)) for h in h1), ""),
        ("URL dễ đọc (slug)", "/bai-hat/" in url and "-" in url, url),
        ("Có canonical", 'rel="canonical"' in page_html, ""),
        ("Có dữ liệu cấu trúc JSON-LD", "application/ld+json" in page_html, "schema.org MusicRecording"),
        ("Có thẻ Open Graph", 'property="og:title"' in page_html, ""),
        ("Ảnh có thuộc tính alt", not no_alt, f"{len(imgs)} ảnh, {len(no_alt)} thiếu alt"),
        ("Có liên kết nội bộ (>= 3)", internal >= 3, f"{internal} liên kết"),
        ("Phản hồi nhanh (< 300 ms)", ms < 300, f"{ms:.0f} ms"),
        ("Có liên kết trỏ vào (off-page)", inlinks > 0, f"{inlinks} trang trỏ vào; PageRank top {pagerank_pct:.0f}%"),
    ]
    passed = sum(ok for _, ok, _ in checks)
    return {"score": round(100 * passed / len(checks)), "checks": [{"name": n, "ok": bool(ok), "detail": d}
                                                                  for n, ok, d in checks]}


def sitemap(base: str, songs, lastmod: str) -> str:
    rows = [f"  <url><loc>{base}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>"]
    for s in songs:
        rows.append(f"  <url><loc>{html.escape(song_url(base, s))}</loc><lastmod>{lastmod}</lastmod>"
                    f"<priority>0.6</priority></url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(rows) + "\n</urlset>\n")


def robots(base: str) -> str:
    return ("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /go\n"
            f"Sitemap: {base}/sitemap.xml\n")
