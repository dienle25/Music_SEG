/* Phòng lab: mỗi ô gọi /api/lab/<công cụ> và vẽ kết quả. */
(function () {
  "use strict";
  const esc = (s) => String(s === null || s === undefined ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const f3 = (x) => (typeof x === "number" ? x.toFixed(3) : "–");
  const bar = (p, w) => `<span class="pbar" style="width:${Math.max(2, Math.round((w || 120) * p))}px"></span>`;
  const table = (head, rows) => `<div class="tbl-wrap"><table class="table table-sm data"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  const chips = (arr) => arr.map((x) => `<span class="badge badge-soft me-1">${x}</span>`).join("");
  const blue = (a) => `background: rgba(42,120,214,${(0.06 + 0.9 * a).toFixed(2)}); color: ${a > 0.55 ? "#fff" : "inherit"}`;

  function tree(t) {
    if (!t) return "";
    if (t.word !== undefined) return `<li><span class="nt">${esc(t.label)}</span> <span class="tok">${esc(t.word)}</span></li>`;
    return `<li><span class="nt">${esc(t.label)}</span><ul>${(t.children || []).map(tree).join("")}</ul></li>`;
  }

  const R = {
    edit(d) {
      const A = d.a, B = d.b, M = d.matrix;
      const path = new Set(["0,0"]);
      let i = 0, j = 0;
      for (const [op] of d.ops) {
        if (op === "giữ" || op === "thay") { i++; j++; } else if (op === "đổi chỗ") { i += 2; j += 2; } else if (op === "xóa") { i++; } else { j++; }
        path.add(i + "," + j);
      }
      let h = `<table class="dp mb-2"><tr><th></th><th>#</th>${[...B].map((c) => `<th>${esc(c === " " ? "␣" : c)}</th>`).join("")}</tr>`;
      for (let r = 0; r < M.length; r++) {
        h += `<tr><th>${r === 0 ? "#" : esc(A[r - 1] === " " ? "␣" : A[r - 1])}</th>` +
          M[r].map((v, c) => `<td class="${path.has(r + "," + c) ? "path" : ""}">${Number.isInteger(v) ? v : v.toFixed(1)}</td>`).join("") + "</tr>";
      }
      h += "</table>";
      const ops = d.ops.filter(([op]) => op !== "giữ").map(([op, x, y]) => `${op} ${esc(x)}${y ? "→" + esc(y) : ""}`);
      return `<div class="mb-1">Khoảng cách: <b>${d.distance}</b>${ops.length ? " · " + chips(ops) : ""}</div><div class="tbl-wrap">${h}</div>`;
    },
    spell(d) {
      const nb = (d.nbest || []).map((x, k) => [k + 1, `<b>${esc(x.text)}</b>`, f3(x.score), f3(x.channel_cost),
        (x.edits || []).map((e) => `${esc(e.from)}→${esc(e.to)} (${esc(e.kind)})`).join(", ") || "không sửa"]);
      const c = (d.candidates || []).map((t) => [esc(t.tok), t.cands.map(([w, cost, kind]) => `${esc(w)} <span class="text-muted">${cost} ${esc(kind)}</span>`).join(" · ")]);
      return `<div class="fw-semibold">Các câu sửa tốt nhất</div>${table(["#", "Câu", "Điểm", "Chi phí lỗi", "Sửa"], nb)}
        <div class="fw-semibold">Ứng viên của từng âm tiết (chi phí theo loại lỗi)</div>${table(["Âm tiết", "Ứng viên"], c)}`;
    },
    lm(d) {
      const pp = d.pp || [];
      const tri = pp[2] || { tokens: [] };
      const rows = tri.tokens.map((t) => [esc(t.w), bar(Math.min(1, t.p * 4), 100) + t.p.toExponential(2), t.logp]);
      return `<div>Perplexity: ${pp.map((x) => `bậc ${x.order}: <b>${x.perplexity}</b>`).join(" · ")}
          ${d.shuffled ? ` · câu bị xáo trộn (bậc 3): <b>${d.shuffled}</b>` : ""}</div>
        <div class="text-muted mb-1">Perplexity càng thấp, câu càng "giống lời bài hát"; xáo trộn thứ tự từ làm perplexity tăng vọt.</div>
        ${table(["Từ", "P(từ | 2 từ trước), bậc 3", "log P"], rows)}
        <div class="fw-semibold">Từ kế tiếp hay gặp</div><div>${(d.next || []).map(([w, p]) => `${esc(w)} <span class="text-muted">${p}</span>`).join(" · ")}</div>
        <div class="fw-semibold mt-1">Sinh tiếp câu (3 lần lấy mẫu)</div>${(d.generate || []).map((g) => `<div>${esc(g)}</div>`).join("")}`;
    },
    restore(d) {
      const nb = (d.nbest || []).map((x, k) => [k + 1, `<b>${esc(x.text)}</b>`, x.logp]);
      const tb = (d.table || []).map((t) => [esc(t.tok), t.cands.map(([w, c]) => `${esc(w)} <span class="text-muted">${c}</span>`).join(" · ")]);
      const ord = d.orders || {};
      return `<div class="fw-semibold">5 cách thêm dấu tốt nhất (trigram)</div>${table(["#", "Câu", "log P"], nb)}
        <div class="fw-semibold">So sánh bậc mô hình</div>
        <div>bậc 1 (dạng hay gặp nhất): ${esc(ord[1])}</div><div>bậc 2: ${esc(ord[2])}</div><div>bậc 3: <b>${esc(ord[3])}</b></div>
        <div class="fw-semibold mt-2">Các dạng có dấu của từng âm tiết (số lần gặp trong kho)</div>${table(["Không dấu", "Dạng có dấu"], tb)}`;
    },
    sentiment(d) {
      const L = d.lexicon || {};
      const hits = (L.hits || []).map((h) => [esc(h.word), h.base, h.value, (h.notes || []).join(", ")]);
      const nb = d.nb || {};
      const words = (nb.words || []).slice(0, 12).map((w) => [esc(w.w), w["tích cực"], w["tiêu cực"]]);
      const top = d.nb_top || {};
      return `<div>Từ điển: điểm <b>${L.score}</b> → <b>${esc(L.label)}</b></div>
        ${table(["Từ", "Điểm gốc", "Sau điều chỉnh", "Lý do"], hits)}
        <div>Cảm xúc: ${Object.entries(d.emotions || {}).map(([k, v]) => `${esc(k)} ×${v}`).join(", ") || "–"}
          · Hàm ý: ${(d.connotations || []).map((c) => `${esc(c.word)} (${c.polarity > 0 ? "+" : ""}${c.polarity})`).join(", ") || "–"}</div>
        <div class="fw-semibold mt-2">Naive Bayes: ${Object.entries(nb.proba || {}).map(([k, v]) => `${esc(k)} ${(100 * v).toFixed(1)}%`).join(" · ")}</div>
        <div class="text-muted">log P(lớp) ban đầu: ${Object.entries(nb.prior || {}).map(([k, v]) => `${esc(k)} ${v}`).join(", ")};
          học từ ${(d.nb_stats || {}).train_lines || "?"} dòng có nhãn yếu.</div>
        ${table(["Đặc trưng", "log P(w | tích cực)", "log P(w | tiêu cực)"], words)}
        <div class="text-muted">Đặc trưng mạnh nhất: ${Object.entries(top).map(([c, l]) => `<b>${esc(c)}</b>: ${l.slice(0, 6).map(([w]) => esc(w)).join(", ")}`).join(" · ")}</div>`;
    },
    qtype(d) {
      return `<div>Loại: <b>${esc(d.type)}</b> <span class="text-muted">(${esc(d.source || "")})</span></div>` +
        Object.entries(d.proba || {}).map(([t, p]) => `<div><span class="d-inline-block" style="width:2.5em">${t}</span>${bar(p, 160)}${(100 * p).toFixed(1)}%</div>`).join("");
    },
    embed(d) {
      const near = (d.nearest || []).map(([w, s]) => [esc(w), bar(Math.max(0, s), 120) + s]);
      const st = d.stats || {};
      return `<div class="row"><div class="col-md-6"><div class="fw-semibold">Gần “${esc(d.word)}” nhất</div>${near.length ? table(["Từ", "cosine"], near) : '<div class="text-muted">Từ này không có trong từ vựng (ít hơn 5 lần).</div>'}</div>
        <div class="col-md-6"><div class="fw-semibold">Phép tương tự</div>${(d.analogy || []).length ? (d.analogy.map(([w, s]) => `<div>${esc(w)} <span class="text-muted">${s}</span></div>`).join("")) : '<div class="text-muted">Gõ ba từ a b c để tìm d sao cho a : b như c : d.</div>'}
        <div class="text-muted mt-2">Từ vựng ${st.vocab || "?"} từ, ${st.dim || "?"} chiều${st.nonzero_ppmi ? ", " + st.nonzero_ppmi + " ô PPMI > 0" : ""}.</div></div></div>`;
    },
    cluster(d) {
      const km = (d.kmeans || []).map((r) => [r.iter, r.sse, r.changed, r.sizes.join(" / ")]);
      const ag = (d.agglomerative || []).map((s) => [s.step, s.dist, esc(s.a.join(", ")), esc(s.b.join(", "))]);
      const ds = d.distances || {};
      const kc = ((d.k_choice || {}).rows || []).map((r) => [r.k, r.silhouette, r.sse]);
      return `<div class="row"><div class="col-md-6"><div class="fw-semibold">K-means (K = 3) từng vòng</div>${table(["Vòng", "SSE", "Số điểm đổi cụm", "Kích thước cụm"], km)}</div>
        <div class="col-md-6"><div class="fw-semibold">Chọn K bằng silhouette (toàn kho)</div>${table(["K", "Silhouette", "SSE"], kc)}</div></div>
        <div class="fw-semibold">Phân cấp average-link: 11 bước gộp đầu (12 bài)</div>${table(["Bước", "Khoảng cách", "Cụm A", "Cụm B"], ag)}
        <div class="fw-semibold">So sánh độ đo cho hai bài: ${esc((d.pair || []).join(" và "))}</div>
        <div>Euclid ${ds.euclid} · cosine ${ds.cosine_sim} (khoảng cách ${ds.cosine_dist}) · Jaccard trên âm tiết ${ds.jaccard} · Pearson ${ds.pearson}</div>
        <div class="fw-semibold mt-1">DBSCAN (eps = 0,45, cosine)</div><div>${d.dbscan.n_clusters} cụm, ${d.dbscan.n_noise} điểm nhiễu; vai trò: ${Object.entries(d.dbscan.roles).map(([k, v]) => `${esc(k)} ${v}`).join(", ")}</div>`;
    },
    attn(d) {
      const t = d.tokens || [];
      if (!t.length) return '<div class="text-muted">Câu trống.</div>';
      let h = `<table class="attn"><tr><th></th>${t.map((w) => `<th class="small">${esc(w)}</th>`).join("")}</tr>`;
      d.weights.forEach((row, i) => {
        h += `<tr><th class="small text-end pe-1">${esc(t[i])}</th>${row.map((a) => `<td style="${blue(a)}" title="${esc(t[i])} → ${a}">${a >= 0.1 ? a.toFixed(2) : ""}</td>`).join("")}</tr>`;
      });
      return `<div class="tbl-wrap">${h}</table></div>${(d.oov || []).length ? `<div class="text-muted">Không có vector (dùng 0): ${esc(d.oov.join(", "))}</div>` : ""}`;
    },
    mask(d) {
      if (d.error) return `<div class="text-danger">${esc(d.error)}</div>`;
      return `<div class="mb-1">${esc(d.left)} <b>___</b> ${esc(d.right)}</div>` +
        (d.candidates || []).map((c) => `<div><span class="d-inline-block" style="width:7em">${esc(c.w)}</span>${bar(c.p, 160)}${(100 * c.p).toFixed(1)}%</div>`).join("");
    },
    parse(d) {
      const ch = (d.chunks || []).map((c, k) => `<span class="badge badge-soft me-1">${k}: ${esc(c.text)} <i>${esc(c.kind)}</i></span>`).join("");
      const deps = (d.deps || []).map((x) => [x.i, esc(x.word), x.head < 0 ? "ROOT" : x.head + " (" + esc((d.deps[x.head] || {}).word || "") + ")", esc(x.rel)]);
      const tr = (d.transitions || []).map((s, k) => [k + 1, esc(s.stack.join(" | ")), esc(s.buffer.join(" | ")), esc(s.action), esc(s.arc)]);
      const chart = (d.chart || []).filter((c) => c.items.length).slice(0, 40).map((c) => [`[${c.i}, ${c.j})`, c.items.map(([a, lp]) => `${esc(a)} <span class="text-muted">${lp}</span>`).join(" · ")]);
      const mc = d.model_check || {};
      const lg = d.logic || {};
      const slots = Object.entries(d.slots || {}).filter(([k, v]) => v && k !== "artist_text").map(([k, v]) => `${k}: ${esc(typeof v === "object" ? v.name || JSON.stringify(v) : v)}`);
      return `<div class="mb-1">Cụm: ${ch}</div>
        <div class="row"><div class="col-md-6"><div class="fw-semibold">Cây thành phần tốt nhất (log P = ${d.logprob})</div>
          <div class="tree mono"><ul>${tree(d.tree)}</ul></div>
          <div class="text-muted">${Object.entries(d.alternatives || {}).map(([k, v]) => `S → ${k}: ${v}`).join(" · ")}</div></div>
        <div class="col-md-6"><div class="fw-semibold">Khe ngữ nghĩa</div><div>${chips(slots)}</div>
          <div class="fw-semibold mt-2">Dạng logic</div><div class="mono">${esc(lg.formula || "")}</div>
          ${(lg.steps || []).map((s) => `<div class="text-muted small">${esc(s)}</div>`).join("")}
          <div class="fw-semibold mt-2">Kiểm tra trên kho (${mc.n_songs || "?"} bài)</div>
          ${(mc.predicates || []).map((p) => `<div class="mono">${esc(p.predicate)}: ${p.count} bài</div>`).join("")}
          <div>Thỏa cả công thức: <b>${mc.satisfied}</b> bài ${(mc.examples || []).map((e) => `· <a href="/bai-hat/${e.id}">${esc(e.title)}</a>`).join(" ")}</div></div></div>
        <div class="row mt-2"><div class="col-md-5"><div class="fw-semibold">Cây phụ thuộc</div>${table(["#", "Cụm", "Đầu ngữ", "Quan hệ"], deps)}</div>
          <div class="col-md-7"><div class="fw-semibold">Dãy chuyển arc-standard (oracle)</div>${table(["#", "Ngăn xếp", "Bộ đệm", "Hành động", "Cung"], tr)}</div></div>
        <details><summary>Bảng CKY (mỗi ô: các ký hiệu và log P tốt nhất)</summary>${table(["Khoảng", "Ký hiệu"], chart)}</details>`;
    },
    ie(d) {
      const ents = (d.entities || []).map((e) => [esc(e.type), esc(e.text), esc(e.value)]);
      const rel = (d.relations || []).map((r) => [esc(r.subject), esc(r.relation), esc(r.object), esc(r.line)]);
      const ev = (d.events || []).map((e) => [esc(e.type), esc(e.trigger), esc(e.agent || "–"), esc(e.patient || "–"), esc(e.place || "–"), esc(e.time || "–"), e.negated ? "có" : ""]);
      return `<div class="fw-semibold">Thực thể</div>${ents.length ? table(["Loại", "Chuỗi", "Chuẩn hóa"], ents) : '<div class="text-muted">Không có.</div>'}
        <div class="fw-semibold">Quan hệ</div>${rel.length ? table(["Chủ thể", "Quan hệ", "Đối tượng", "Dòng"], rel) : '<div class="text-muted">Không có.</div>'}
        <div class="fw-semibold">Sự kiện</div>${ev.length ? table(["Loại", "Từ kích hoạt", "Ai", "Đối tượng", "Ở đâu", "Khi nào", "Phủ định"], ev) : '<div class="text-muted">Không có.</div>'}`;
    },
    time(d) {
      const tags = (d.tags || []).map((t) => [esc(t.text), esc(t.type), esc(t.kind), `<span class="mono">${esc(t.value)}</span>`]);
      const lines = (d.lines || []).map((l) => [esc(l.line), esc(l.tense || "–")]);
      return `${table(["Biểu thức", "Loại", "Kiểu", "Giá trị"], tags)}${table(["Dòng", "Thì"], lines)}`;
    },
    wsd(d) {
      const rows = (d.words || []).map((x) => [esc(x.word), x.ambiguous ? "nhiều nghĩa" : "một nghĩa",
        `<b>${esc(x.sense || "")}</b> ${esc(x.gloss || "")}`,
        (x.senses || []).map((s) => `${esc(s.synset)}: ${s.overlap}`).join(" · "), esc((x.synonyms || []).join(", "))]);
      const rel = Object.values(d.relations || {}).map((r) => [esc(r.synset), esc(r.hypernym_path.join(" → ")), esc((r.hyponyms || []).join(", ")), esc(r.antonym || "–"), esc([r.part_of].concat(r.parts || []).filter(Boolean).join(", ") || "–")]);
      return `${table(["Từ", "", "Nghĩa Lesk chọn", "Điểm trùng của các nghĩa", "Đồng nghĩa"], rows)}
        <div class="fw-semibold">Quan hệ trong WordNet</div>${table(["Nghĩa", "Thượng vị", "Hạ vị", "Trái nghĩa", "Bộ phận"], rel)}
        <div>Mở rộng truy vấn: <b>${esc(d.expansion || "–")}</b></div>`;
    },
    index(d) {
      const p = d.posting || {};
      const rows = (p.rows || []).map((r) => [esc(r.doc), r.docnum, r.gap, r.tf, esc((r.positions || []).join(",")), `<span class="mono">${esc(r.vbyte)}</span>`, `<span class="mono">${esc(r.gamma)}</span>`]);
      let h = `<div>Từ <b>${esc(p.term || "")}</b> có trong ${p.df || 0} bài · danh sách docid: ${p.bytes_raw} byte chưa nén → v-byte ${p.bytes_vbyte} byte, gamma ${p.bytes_gamma} byte</div>
        ${table(["Bài", "docid", "Khoảng cách", "tf", "Vị trí", "v-byte", "Elias-gamma"], rows)}`;
      if (d.intersect) {
        h += `<div class="fw-semibold">Giao hai danh sách (${d.intersect.len_a} và ${d.intersect.len_b} mục): ${d.intersect.result} bài chung</div>
          <div>Có con trỏ nhảy: <b>${d.intersect.comparisons}</b> phép so sánh · không có: <b>${d.intersect_noskip.comparisons}</b> phép so sánh</div>`;
      }
      return h;
    },
    daat(d) {
      const t = (x) => table(["Bài", "Điểm"], (x.top || []).slice(0, 8).map(([id, s]) => [`<a href="/bai-hat/${esc(id)}">${esc(id)}</a>`, s]));
      return `<div class="row"><div class="col-md-6"><div class="fw-semibold">TAAT: ${d.taat.accumulators} bộ tích lũy, ${d.taat.ops} phép cộng, ${d.taat.ms} ms</div>${t(d.taat)}</div>
        <div class="col-md-6"><div class="fw-semibold">DAAT: đống ${d.daat.heap} phần tử, ${d.daat.ops} bước, ${d.daat.ms} ms</div>${t(d.daat)}</div></div>
        <div class="text-muted">Hai cách cho cùng kết quả; khác nhau ở bộ nhớ và thứ tự duyệt.</div>`;
    },
    boolean(d) {
      return `<div>${(d.explain || []).map((e) => `<span class="badge badge-soft me-1">${esc(e.op)} ${esc(e.term)} (${e.df} bài)</span>`).join("")}</div>
        <div class="mt-1">Kết quả: <b>${d.n}</b> bài</div>${(d.docs || []).map((x) => `<div><a href="/bai-hat/${esc(x.id)}">${esc(x.title)}</a></div>`).join("")}`;
    },
    assoc(d) {
      if (!d || d.missing || !d.term) return '<div class="text-muted">Từ này không có trong chỉ mục.</div>';
      const col = (name, arr) => `<div class="col-md-3"><div class="fw-semibold">${name}</div>${(arr || []).map((x) => `<div>${esc(x.term)} <span class="text-muted">${x.score}</span></div>`).join("")}</div>`;
      return `<div class="mb-1">“${esc(d.term)}” có trong ${d.n_a}/${d.N} bài.</div><div class="row">${col("Dice", d.dice)}${col("MIM", d.mim)}${col("EMIM", d.emim)}${col("chi²", d.chi2)}</div>`;
    },
  };

  async function run(form) {
    const tool = form.dataset.tool;
    const out = form.parentElement.querySelector(".lab-out");
    const params = new URLSearchParams(new FormData(form));
    out.innerHTML = '<div class="text-muted">Đang chạy…</div>';
    try {
      const r = await fetch(`/api/lab/${tool}?` + params.toString());
      const d = await r.json();
      out.innerHTML = r.ok ? R[tool](d) : `<div class="text-danger">${esc(d.error || r.status)}</div>`;
    } catch (e) {
      out.innerHTML = `<div class="text-danger">Lỗi: ${esc(e.message)}</div>`;
    }
  }
  document.querySelectorAll(".lab-form").forEach((f) => {
    f.addEventListener("submit", (e) => { e.preventDefault(); run(f); });
  });
  // chạy sẵn ô đang được xem (theo #neo trên URL), các ô khác chạy khi bấm
  const first = location.hash ? document.querySelector(location.hash + " .lab-form") : null;
  if (first) run(first);
})();
