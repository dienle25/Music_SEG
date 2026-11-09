"""Biểu đồ SVG nhỏ vẽ phía máy chủ cho trang Đánh giá và Thống kê (không cần thư viện JS).

Quy ước: nét 2px, điểm 8px, lưới nhạt, chữ dùng màu chữ (không dùng màu của chuỗi), có chú giải khi
có từ 2 chuỗi trở lên và nhãn trực tiếp ở cuối đường; di chuột lên điểm / cột để xem số (thẻ <title>).
"""
from __future__ import annotations

import math

from markupsafe import Markup, escape

COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"]


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = mag
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if span / step <= n:
            break
    v = math.ceil(lo / step - 1e-9) * step
    out = []
    while v <= hi + 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def _fmt(fmt, v):
    if callable(fmt):
        return fmt(v)
    try:
        return fmt.format(v)
    except Exception:
        return str(v)


def _short(name: str, n: int = 26) -> str:
    return name if len(name) <= n else name[: n - 1].rstrip() + "…"


def legend(names) -> str:
    if len(names) < 2:
        return ""
    items = "".join(f'<span class="sw" style="background:{COLORS[i % len(COLORS)]}"></span>{escape(n)}'
                    for i, n in enumerate(names))
    return f'<div class="legend mb-1">{items}</div>'


def line_chart(xs, series, width=560, height=250, ymin=None, ymax=None, xfmt="{}", yfmt="{:.2f}",
               xlabel="", ylabel="", label="Biểu đồ đường") -> Markup:
    """series: [(tên, [giá trị theo xs])]. Giá trị None được bỏ qua."""
    series = [(n, list(v)) for n, v in series]
    vals = [y for _, v in series for y in v if y is not None]
    if not xs or not vals:
        return Markup('<div class="text-muted small">Chưa có số liệu.</div>')
    lo = min(vals) if ymin is None else ymin
    hi = max(vals) if ymax is None else ymax
    if hi <= lo:
        hi = lo + 1
    multi = len(series) > 1
    ends_txt = [f"{_short(n)} {_fmt(yfmt, next((y for y in reversed(v) if y is not None), 0))}" for n, v in series]
    ml, mt, mb = 48, 12, 42
    mr = min(width // 2, 30 + int(6.6 * max(len(t) for t in ends_txt))) if multi else 18   # đủ chỗ cho nhãn cuối đường
    pw, ph = width - ml - mr, height - mt - mb
    xnum = all(isinstance(x, (int, float)) for x in xs)
    if xnum and len(xs) > 1:
        x0, x1 = min(xs), max(xs)
        X = lambda x: ml + pw * (x - x0) / ((x1 - x0) or 1)
    else:
        X = lambda x, idx={x: i for i, x in enumerate(xs)}: ml + pw * idx[x] / max(1, len(xs) - 1)
    Y = lambda y: mt + ph * (1 - (y - lo) / (hi - lo))
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="{width}" role="img" aria-label="{escape(label)}">']
    out.append('<g class="grid">')
    for t in _ticks(lo, hi, 5):
        out.append(f'<line x1="{ml}" x2="{ml + pw}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>')
    out.append("</g><g class=\"axis\">")
    for t in _ticks(lo, hi, 5):
        out.append(f'<text x="{ml - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{escape(_fmt(yfmt, t))}</text>')
    step = max(1, len(xs) // 10)
    shown = list(range(0, len(xs), step))
    if shown[-1] != len(xs) - 1:                # luôn ghi giá trị cuối, bỏ nhãn liền trước nếu quá sát
        if len(xs) - 1 - shown[-1] < step and len(shown) > 1:
            shown.pop()
        shown.append(len(xs) - 1)
    for i in shown:
        x = xs[i]
        out.append(f'<text x="{X(x):.1f}" y="{mt + ph + 16}" text-anchor="middle">{escape(_fmt(xfmt, x))}</text>')
    if xlabel:
        out.append(f'<text x="{ml + pw / 2:.1f}" y="{height - 6}" text-anchor="middle">{escape(xlabel)}</text>')
    if ylabel:
        out.append(f'<text x="12" y="{mt + ph / 2:.1f}" text-anchor="middle" transform="rotate(-90 12 {mt + ph / 2:.1f})">'
                   f'{escape(ylabel)}</text>')
    out.append(f'<line x1="{ml}" x2="{ml + pw}" y1="{mt + ph}" y2="{mt + ph}" stroke="#c4c2c1"/></g>')
    ends = []
    for si, (name, v) in enumerate(series):
        c = COLORS[si % len(COLORS)]
        pts = [(X(x), Y(y), x, y) for x, y in zip(xs, v) if y is not None]
        if not pts:
            continue
        d = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py, _, _ in pts)
        out.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="2" stroke-linejoin="round"/>')
        for px, py, x, y in pts:
            tip = f"{name} · {_fmt(xfmt, x)}: {_fmt(yfmt, y)}"
            out.append(f'<circle class="pt" cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{c}"/>'
                       f'<circle class="hit" cx="{px:.1f}" cy="{py:.1f}" r="11"><title>{escape(tip)}</title></circle>')
        ends.append([pts[-1][1], name, c, pts[-1][3]])
    if multi:                                   # nhãn trực tiếp ở cuối đường, đẩy ra để không chồng nhau
        ends.sort(key=lambda e: e[0])
        for i in range(1, len(ends)):
            if ends[i][0] - ends[i - 1][0] < 14:
                ends[i][0] = ends[i - 1][0] + 14
        for y, name, c, last in ends:
            x = ml + pw + 8
            out.append(f'<line x1="{x}" x2="{x + 12}" y1="{y:.1f}" y2="{y:.1f}" stroke="{c}" stroke-width="2"/>'
                       f'<text class="lbl" x="{x + 16}" y="{y + 4:.1f}">{escape(_short(name))} {escape(_fmt(yfmt, last))}</text>')
    out.append("</svg>")
    return Markup(legend([n for n, _ in series]) + "".join(out))


def bar_chart(labels, values, width=560, height=220, yfmt="{:.2f}", xlabel="", ylabel="", label="Biểu đồ cột",
              highlight_max=True) -> Markup:
    vals = [v or 0 for v in values]
    if not labels:
        return Markup('<div class="text-muted small">Chưa có số liệu.</div>')
    hi = max(vals) or 1
    ml, mr, mt, mb = 48, 12, 18, 42
    pw, ph = width - ml - mr, height - mt - mb
    band = pw / len(labels)
    bw = max(4, min(28, band * 0.55))
    Y = lambda y: mt + ph * (1 - y / hi)
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="{width}" role="img" aria-label="{escape(label)}">',
           '<g class="grid">']
    for t in _ticks(0, hi, 4):
        out.append(f'<line x1="{ml}" x2="{ml + pw}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>')
    out.append('</g><g class="axis">')
    for t in _ticks(0, hi, 4):
        out.append(f'<text x="{ml - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{escape(_fmt(yfmt, t))}</text>')
    step = max(1, len(labels) // 14)
    for i, l in enumerate(labels):
        if i % step == 0:
            out.append(f'<text x="{ml + band * (i + .5):.1f}" y="{mt + ph + 16}" text-anchor="middle">{escape(str(l))}</text>')
    if xlabel:
        out.append(f'<text x="{ml + pw / 2:.1f}" y="{height - 6}" text-anchor="middle">{escape(xlabel)}</text>')
    if ylabel:
        out.append(f'<text x="12" y="{mt + ph / 2:.1f}" text-anchor="middle" transform="rotate(-90 12 {mt + ph / 2:.1f})">'
                   f'{escape(ylabel)}</text>')
    out.append('</g>')
    imax = max(range(len(vals)), key=lambda i: vals[i])
    for i, (l, v) in enumerate(zip(labels, vals)):
        x = ml + band * (i + .5) - bw / 2
        y = Y(v)
        h = mt + ph - y
        r = min(4, bw / 2, h)
        if h > 0.5:
            d = (f"M{x:.1f},{mt + ph:.1f} L{x:.1f},{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
                 f"L{x + bw - r:.1f},{y:.1f} Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f} L{x + bw:.1f},{mt + ph:.1f} Z")
            out.append(f'<path d="{d}" fill="var(--series-1)"/>')
        out.append(f'<rect class="hit" x="{ml + band * i:.1f}" y="{mt}" width="{band:.1f}" height="{ph}">'
                   f'<title>{escape(str(l))}: {escape(_fmt(yfmt, v))}</title></rect>')
        if highlight_max and i == imax and v > 0:
            out.append(f'<text class="val" x="{x + bw / 2:.1f}" y="{y - 5:.1f}" text-anchor="middle">{escape(_fmt(yfmt, v))}</text>')
    out.append(f'<line x1="{ml}" x2="{ml + pw}" y1="{mt + ph}" y2="{mt + ph}" stroke="#c4c2c1"/></svg>')
    return Markup("".join(out))
