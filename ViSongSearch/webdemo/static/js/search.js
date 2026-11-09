/* ViSongSearch: trang tìm kiếm.
 *
 * Chỉnh từ typesense/showcase-songs-search (src/app.js), Copyright Typesense, Apache License 2.0.
 * Thay đổi: bỏ Typesense adapter, jQuery và Google Analytics; searchClient gọi API của chính demo
 * (FastAPI, trả kết quả đúng định dạng InstantSearch); bộ lọc tâm trạng, nhóm kết quả, chủ đề, thời gian;
 * chọn mô hình xếp hạng bằng sortBy; bảng "Vì sao ra kết quả này?" lấy từ userData của kết quả;
 * gợi ý truy vấn từ nhật ký; giao diện tiếng Việt.
 */
(function () {
  "use strict";
  const INDEX = "songs";
  const W = instantsearch.widgets;
  const MODELS = (window.VSS && window.VSS.models) || {};

  async function post(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error("Máy chủ trả lỗi " + r.status);
    return r.json();
  }

  function emptyResult(r) {
    return {
      hits: [], nbHits: 0, nbPages: 0, page: 0, hitsPerPage: (r.params && r.params.hitsPerPage) || 0,
      processingTimeMS: 0, facets: {}, exhaustiveNbHits: true, query: "", params: "", index: r.indexName,
    };
  }

  // searchClient theo giao diện của InstantSearch: search() và searchForFacetValues()
  const searchClient = {
    search(requests) {
      if (requests.every((r) => !(r.params && (r.params.query || "").trim()))) {
        return Promise.resolve({ results: requests.map(emptyResult) });
      }
      return post("/api/1/indexes/*/queries", { requests });
    },
    searchForFacetValues(requests) {
      return post("/api/sffv", { requests });
    },
  };

  // URL gọn: /?q=...&m=bm25&mood=vui
  const LISTS = { artists: "ca-si", topic: "chu-de", time_tags: "thoi-gian", source: "nguon", group: "nhom" };
  const stateMapping = {
    stateToRoute(uiState) {
      const s = uiState[INDEX] || {};
      const r = { q: s.query || undefined };
      if (s.sortBy && s.sortBy !== INDEX) r.m = s.sortBy.split("/")[1];
      if (s.menu && s.menu.mood) r.mood = s.menu.mood;
      for (const [attr, key] of Object.entries(LISTS)) {
        if (s.refinementList && s.refinementList[attr] && s.refinementList[attr].length) r[key] = s.refinementList[attr];
      }
      return r;
    },
    routeToState(r) {
      const s = { query: r.q || "" };
      if (r.m && MODELS[r.m]) s.sortBy = INDEX + "/" + r.m;
      if (r.mood) s.menu = { mood: r.mood };
      for (const [attr, key] of Object.entries(LISTS)) {
        if (r[key]) {
          s.refinementList = s.refinementList || {};
          s.refinementList[attr] = [].concat(r[key]);
        }
      }
      return { [INDEX]: s };
    },
  };

  // có truy vấn thì hiện kết quả, không thì hiện phần giới thiệu (cả khi mở thẳng link /?q=...)
  function showResults(uiState) {
    const q = (((uiState || {})[INDEX] || {}).query || "").trim();
    document.getElementById("results-section").classList.toggle("d-none", q === "");
    document.getElementById("intro").classList.toggle("d-none", q !== "");
  }

  const search = instantsearch({
    searchClient,
    indexName: INDEX,
    routing: { router: instantsearch.routers.history({ cleanUrlOnDispose: true }), stateMapping },
    onStateChange({ uiState, setUiState }) {
      showResults(uiState);
      setUiState(uiState);
    },
    future: { preserveSharedStateOnUnmount: true },
  });

  const listClasses = {
    searchableInput: "form-control form-control-sm mb-2 border-light-2",
    searchableSubmit: "d-none",
    searchableReset: "d-none",
    showMore: "btn btn-secondary btn-sm",
    list: "list-unstyled",
    count: "badge bg-light text-bg-light ms-2",
    label: "d-flex align-items-center mb-2",
    checkbox: "me-2",
  };
  const facetList = (container, attribute, extra) =>
    W.refinementList(Object.assign({ container, attribute, limit: 8, showMore: true, showMoreLimit: 20,
      cssClasses: listClasses,
      templates: { showMoreText: ({ isShowingMore }) => (isShowingMore ? "Thu gọn" : "Xem thêm") },
    }, extra || {}));

  const moodClass = (m) => (m === "vui" ? "mood-vui" : m === "buồn" ? "mood-buon" : "mood-tt");
  const pct = (p) => Math.round(100 * p) + "%";

  // ------------------------------------------------------------------ bảng "Vì sao ra kết quả này?"
  let whyOpen = true;
  try { whyOpen = localStorage.getItem("vss-why") !== "0"; } catch (e) { /* trình duyệt chặn bộ nhớ */ }

  function renderWhy(x, html) {
    const proba = Object.entries(x.type_proba || {}).slice(0, 5);
    const slots = Object.entries(x.slots || {}).filter(([k, v]) => v && k !== "intent");
    const slotName = { artist: "ca sĩ", lyric: "lời", about: "nói về", mood: "tâm trạng", time: "thời gian" };
    const slotVal = (v) => (typeof v === "object" ? v.name || JSON.stringify(v) : v);
    const intentName = { lyric: "chỉ là lời bài hát", find_song: "tìm bài theo mô tả", ask_title: "hỏi tên bài" };
    return html`
      <div class="panel why mb-3">
        <div class="why-summary d-flex flex-wrap align-items-center gap-2" data-bs-toggle="collapse" data-bs-target="#why-detail"
             aria-expanded="${whyOpen}" role="button">
          <strong class="text-primary">Vì sao ra kết quả này?</strong>
          <span class="chip"><b>${x.type}</b> ${x.type_name}</span>
          ${x.spell ? html`<span class="chip">đã sửa: <b>${x.spell.text}</b></span>` : ""}
          ${x.restorations && x.restorations.length ? html`<span class="chip">thêm dấu: <b>${x.restorations[0].text}</b></span>` : ""}
          <span class="text-muted">${x.model} · ${x.ms} ms</span>
          <span class="ms-auto text-muted">${whyOpen ? "Thu gọn ▲" : "Xem từng bước ▼"}</span>
        </div>
        <div id="why-detail" class="collapse ${whyOpen ? "show" : ""} mt-2">
          <div class="row">
            <div class="col-lg-6">
              <div class="step"><div class="step-title">1. Nhận loại truy vấn <span class="course tmg">TMG 03-04</span></div>
                ${proba.map(([t, p]) => html`<div><span class="d-inline-block" style="width:2.2em">${t}</span>
                  <span class="pbar" style="width:${Math.max(2, 120 * p)}px"></span>${pct(p)}</div>`)}
                <div class="text-muted">Quyết định: ${x.type} (${x.type_source || "bộ phân loại"})${x.content && x.content !== "" ? html`; phần lời: “${x.content}”` : ""}</div>
              </div>
              ${x.restorations && x.restorations.length ? html`
              <div class="step"><div class="step-title">2. Phục hồi dấu, n-best <span class="course tmg">TMG 02</span><span class="course tmg">TMG 09</span></div>
                ${x.restorations.map((r) => html`<div><span class="pbar" style="width:${Math.max(2, 80 * r.p)}px"></span>${r.text} <span class="text-muted">(p=${r.p})</span></div>`)}
                <div class="text-muted">Mô hình trigram Kneser-Ney chấm từng cách thêm dấu; BM25 trộn cả 5 cách theo xác suất.</div>
              </div>` : ""}
              ${x.spell ? html`
              <div class="step"><div class="step-title">3. Sửa chính tả theo kênh nhiễu <span class="course tmg">TMG 01</span></div>
                <div>Có phải bạn muốn tìm: <b>${x.spell.text}</b></div>
                <div class="text-muted">${(x.spell.edits || []).map((e) => html`<span class="chip">${e.from} → ${e.to} <i>${e.kind}</i></span>`)}</div>
              </div>` : ""}
              <div class="step"><div class="step-title">4. Phân tích câu, dạng logic <span class="course tmg">TMG 11</span><span class="course tmg">TMG 13</span></div>
                ${x.bracket ? html`<div class="mono">${x.bracket}</div>` : ""}
                <div>Ý định: ${intentName[(x.slots || {}).intent] || "—"}</div>
                <div>${slots.map(([k, v]) => html`<span class="chip">${slotName[k] || k}: <b>${slotVal(v)}</b></span>`)}</div>
                ${x.logic ? html`<div class="mono text-muted">${x.logic}</div>` : ""}
              </div>
            </div>
            <div class="col-lg-6">
              ${(x.wsd && x.wsd.length) || x.expansion ? html`
              <div class="step"><div class="step-title">5. Nghĩa của từ, mở rộng đồng nghĩa <span class="course tmg">TMG 16</span></div>
                ${(x.wsd || []).filter((w) => w.ambiguous).slice(0, 3).map((w) => html`<div><b>${w.word}</b>: ${w.gloss}</div>`)}
                ${x.expansion ? html`<div class="text-muted">Thêm vào truy vấn (trọng số 0,3): ${x.expansion}</div>` : ""}
              </div>` : ""}
              ${(x.entities && x.entities.length) || (x.timex && x.timex.length) ? html`
              <div class="step"><div class="step-title">6. Thực thể và thời gian trong câu <span class="course tmg">TMG 14</span><span class="course tmg">TMG 15</span></div>
                ${(x.entities || []).map((e) => html`<span class="chip">${e}</span>`)}
                ${(x.timex || []).map((t) => html`<span class="chip">${t}</span>`)}
              </div>` : ""}
              ${x.runs && x.runs.length ? html`
              <div class="step"><div class="step-title">7. Trộn các danh sách xếp hạng (RRF) <span class="course">SEG 7</span><span class="course">SEG 12</span></div>
                <table class="table table-sm mb-1"><thead><tr><th>Danh sách</th><th class="text-end">Trọng số</th><th>3 bài đầu</th></tr></thead>
                <tbody>${x.runs.map((r) => html`<tr class="${r.weight > 0 ? "" : "text-muted"}"><td>${r.name}</td><td class="text-end">${r.weight}</td><td>${(r.top || []).join(" · ")}</td></tr>`)}</tbody></table>
                <div class="text-muted">Trọng số của loại ${x.type}: ${x.weights_source}.</div>
              </div>` : ""}
              ${x.boosts && Object.keys(x.boosts).length ? html`
              <div class="step"><div class="step-title">8. Điểm cộng theo ràng buộc của câu</div>
                ${Object.entries(x.boosts).map(([k, n]) => html`<span class="chip">${k}: ${n} bài</span>`)}
              </div>` : ""}
              <div class="text-muted">Phân tích câu ${x.ms_analyze} ms, tổng ${x.ms} ms.</div>
            </div>
          </div>
        </div>
      </div>`;
  }

  // ------------------------------------------------------------------ các widget
  search.addWidgets([
    W.searchBox({
      container: "#searchbox",
      showSubmit: false,
      showReset: false,
      placeholder: "Gõ một câu hát bạn nhớ, hoặc kể về bài hát…",
      autofocus: true,
      cssClasses: { input: "form-control searchbox-input" },
      queryHook: (function () {
        let t = null;
        return function (query, run) {
          clearTimeout(t);
          t = setTimeout(() => run(query), 220);
        };
      })(),
    }),
    W.stats({
      container: "#stats",
      templates: {
        text: ({ nbHits, hasNoResults, processingTimeMS }) =>
          hasNoResults ? "Không có kết quả" : `${nbHits.toLocaleString("vi-VN")} bài (đã gom bản trùng) · ${processingTimeMS} ms`,
      },
    }),
    W.infiniteHits({
      container: "#hits",
      cssClasses: {
        list: "list-unstyled grid-container",
        item: "d-flex flex-column search-result-card bg-light-2 p-3",
        loadMore: "btn btn-primary mx-auto d-block mt-4",
        disabledLoadMore: "d-none",
      },
      templates: {
        showMoreText: () => "Xem thêm kết quả",
        item: (hit, { html, components }) => html`
          <div class="d-flex flex-column h-100 ${hit.team ? "team-" + hit.team + " ps-2" : ""}">
            <span class="rank">#${hit.rank}${hit.team ? " · " + (hit.team === "A" ? "ViSongSearch" : "BM25") : ""}</span>
            <h6 class="text-primary fw-light font-letter-spacing-loose mb-0 pe-4">
              <a href="${hit.click}">${components.Highlight({ hit, attribute: "title" })}</a>
            </h6>
            <div>của <a role="button" class="clickable-search-term" data-q="${hit.artists && hit.artists[0] ? hit.artists[0] : hit.artist}">${components.Highlight({ hit, attribute: "artist" })}</a></div>
            <div class="snippet">${components.Snippet({ hit, attribute: "lyrics" })}</div>
            <div class="meta-badges mt-2">
              <span class="badge ${moodClass(hit.mood)}">${hit.mood}</span>
              ${(hit.time_tags || []).slice(0, 2).map((t) => html`<span class="badge badge-soft">${t}</span>`)}
              ${hit.versions > 1 ? html`<span class="badge badge-soft" title="Các bản của cùng bài, gom bằng MinHash">${hit.versions} phiên bản</span>` : ""}
            </div>
            <div class="mt-auto d-flex justify-content-between align-items-end small text-muted pt-2">
              <span title="Thứ hạng PageRank trong đồ thị liên kết đã thu thập">PageRank top ${Math.max(1, 100 - hit.pagerank_pct)}%</span>
              <a href="${hit.url}" target="_blank" rel="noopener nofollow" title="Trang gốc">
                <img src="/static/img/generic_song_link_icon.svg" alt="Trang gốc ${hit.source}" height="13" /> ${hit.source}</a>
            </div>
          </div>`,
        empty: (data, { html }) => html`<div class="mt-3">Không tìm thấy bài nào cho <q>${data.query}</q>.
            Thử bỏ bớt bộ lọc, gõ ít chữ hơn, hoặc hỏi <a href="/tro-ly">trợ lý</a>.</div>`,
      },
    }),
    W.queryRuleCustomData({
      container: "#why",
      templates: { default: ({ items }, { html }) => (items && items[0] ? renderWhy(items[0], html) : "") },
    }),
    W.menu({
      container: "#mood-menu",
      attribute: "mood",
      sortBy: ["count:desc"],
      cssClasses: {
        list: "list-unstyled",
        item: "ps-2 mb-2 text-normal",
        count: "badge bg-light text-bg-light ms-2",
        selectedItem: "bg-secondary p-2 ps-3",
      },
    }),
    facetList("#group-list", "group", { limit: 6, showMore: false }),
    facetList("#artists-list", "artists", { searchable: true, searchablePlaceholder: "Tìm ca sĩ" }),
    facetList("#topic-list", "topic", { showMore: false }),
    facetList("#time-list", "time_tags"),
    facetList("#source-list", "source", { showMore: false }),
    W.configure({ hitsPerPage: 12 }),
    W.sortBy({
      container: "#sort-by",
      items: Object.entries(MODELS).map(([k, label]) => ({ label, value: k === "fusion" ? INDEX : INDEX + "/" + k })),
      cssClasses: { select: "form-select form-select-sm" },
    }),
    W.currentRefinements({
      container: "#current-refinements",
      cssClasses: {
        list: "list-unstyled",
        label: "d-none",
        item: "h6",
        category: "badge bg-light text-bg-light px-3",
        delete: "btn btn-sm btn-link p-0 ps-2",
      },
    }),
  ]);

  function setQuery(q, model) {
    search.setUiState((prev) => {
      const s = { query: q };
      if (model) s.sortBy = INDEX + "/" + model;
      else if (prev[INDEX] && prev[INDEX].sortBy && !prev[INDEX].sortBy.endsWith("/boolean")) s.sortBy = prev[INDEX].sortBy;
      return Object.assign({}, prev, { [INDEX]: s });
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  document.addEventListener("click", (ev) => {
    const ex = ev.target.closest(".clickable-example");
    if (ex) { setQuery(ex.dataset.q, ex.dataset.m || ""); return; }
    const term = ev.target.closest(".clickable-search-term");
    if (term) { setQuery(term.dataset.q || term.textContent.trim(), ""); return; }
  });
  document.addEventListener("shown.bs.collapse", (e) => {
    if (e.target.id === "why-detail") { whyOpen = true; try { localStorage.setItem("vss-why", "1"); } catch (_) {} }
  });
  document.addEventListener("hidden.bs.collapse", (e) => {
    if (e.target.id === "why-detail") { whyOpen = false; try { localStorage.setItem("vss-why", "0"); } catch (_) {} }
  });

  search.start();
  showResults(search.getUiState());

  // gợi ý truy vấn (nhật ký truy vấn đã làm sạch + tên bài), SEG 6
  const input = document.querySelector("#searchbox input");
  const dl = document.getElementById("suggest-list");
  if (input && dl) {
    input.setAttribute("list", "suggest-list");
    input.setAttribute("autocomplete", "off");
    let t = null;
    input.addEventListener("input", () => {
      clearTimeout(t);
      const q = input.value;
      t = setTimeout(async () => {
        if (q.trim().length < 2) { dl.innerHTML = ""; return; }
        try {
          const r = await fetch("/api/suggest?q=" + encodeURIComponent(q));
          const data = await r.json();
          dl.innerHTML = "";
          for (const s of data.suggestions || []) {
            const o = document.createElement("option");
            o.value = s;
            dl.appendChild(o);
          }
        } catch (e) { /* bỏ qua */ }
      }, 150);
    });
  }
})();
