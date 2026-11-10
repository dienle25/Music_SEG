/* Trợ lý hội thoại: gửi từng lượt nói tới /api/chat, hiện câu trả lời và trạng thái NLU / DST / policy. */
(function () {
  "use strict";
  const box = document.getElementById("chat-box");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  let conv = Math.random().toString(36).slice(2, 10);
  let mode = "rule";
  const GREETING = box.innerHTML;
  // Gợi ý theo ngữ cảnh: đổi theo hành động trợ lý vừa làm (policy)
  const SUGGEST = {
    start: ["bài hát buồn về mùa thu", "hình như có câu em của ngày hôm qua", "bài của Mỹ Tâm"],
    hỏi_lyric: ["không nhớ"],
    hỏi_artist: ["của ai cũng được", "không nhớ"],
    hỏi_mood: ["buồn", "vui", "không nhớ"],
    hỏi_time: ["mùa thu", "ban đêm", "không nhớ"],
    đưa_kết_quả: ["đúng rồi", "bài khác", "bài này của ai vậy"],
    đưa_thêm: ["đúng rồi", "bài khác"],
    trả_lời: ["đúng rồi", "bài khác"],
    xác_nhận: ["cảm ơn"],
    không_thấy: ["không nhớ"],
  };
  function suggest(policy) {
    const el = $("chat-suggest");
    el.innerHTML = "";
    (SUGGEST[policy] || SUGGEST.start).forEach((q) => {
      const a = document.createElement("a");
      a.setAttribute("role", "button");
      a.className = "example-chip quick-reply";
      a.textContent = q;
      a.addEventListener("click", () => send(q));
      el.appendChild(a);
    });
  }
  const $ = (id) => document.getElementById(id);

  function add(text, who) {
    const d = document.createElement("div");
    d.className = "chat-msg " + (who === "user" ? "chat-user" : "chat-bot");
    d.textContent = text;
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
    return d;
  }
  const show = (v) => {
    if (v === null || v === undefined || v === "") return "–";
    if (typeof v === "object") {
      const parts = Object.entries(v).filter(([k, x]) => x && k !== "artist_text").map(([k, x]) => k + ": " + (typeof x === "object" ? (x.name || x.text || JSON.stringify(x)) : x));
      return parts.length ? parts.join("; ") : "–";
    }
    return String(v);
  };

  async function send(text) {
    add(text, "user");
    const wait = add("…", "bot");
    try {
      const r = await fetch("/api/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conv, text, mode }),
      });
      const data = await r.json();
      wait.textContent = data.reply;
      $("t-engine").textContent = data.engine || "Thường (luật)";
      suggest(data.policy);
      $("t-intent").textContent = show(data.nlu && data.nlu.intent);
      $("t-nlu").textContent = show(data.nlu && data.nlu.slots);
      $("t-state").textContent = show(data.state);
      $("t-policy").textContent = show(data.policy);
      $("t-query").textContent = show(data.query);
      $("t-conf").textContent = data.confidence === null || data.confidence === undefined ? "–" : data.confidence;
      const res = $("chat-results");
      res.innerHTML = "";
      if (data.results && data.results.length) {
        res.classList.remove("text-muted");
        data.results.forEach((h, i) => {
          const a = document.createElement("a");
          a.href = h.page;
          a.textContent = (i + 1) + ". " + h.title + " · " + h.artist;
          res.appendChild(a);
        });
      } else {
        res.classList.add("text-muted");
        res.textContent = "Chưa có.";
      }
    } catch (e) {
      wait.textContent = "Lỗi kết nối tới máy chủ demo: " + e.message;
    }
  }

  // Ba nút: Thường (luật), Mô hình 1 (mô hình nhỏ), Mô hình 2 (mô hình lớn nhất) trong số mô hình Ollama đã tải
  const sizeOf = (m) => { const x = /(\d+(?:\.\d+)?)b(?![a-z])/i.exec(m.split(":").pop()); return x ? x[1].toUpperCase() + "B" : m; };
  const btns = [...document.querySelectorAll("#chat-mode button")];
  function pick(b) {
    btns.forEach((x) => x.classList.toggle("active", x === b));
    mode = b.dataset.model ? "llm:" + b.dataset.model : "rule";
  }
  btns.forEach((b) => b.addEventListener("click", () => { if (!b.disabled) pick(b); }));
  fetch("/api/llm").then((r) => r.json()).then((d) => {
    const ms = d.models || [];                       // mạnh nhất trước
    const slots = { 2: ms[0], 1: ms.length > 1 ? ms[ms.length - 1] : undefined };
    if (ms.length === 1) { slots[1] = ms[0]; slots[2] = undefined; }
    btns.filter((b) => b.dataset.slot).forEach((b) => {
      const m = slots[b.dataset.slot];
      if (m) {
        b.disabled = false;
        b.dataset.model = m;
        b.textContent = "Mô hình " + b.dataset.slot + " (" + sizeOf(m) + ")";
        b.title = m + " chạy bằng Ollama trên máy";
      }
    });
    if (!ms.length) $("mode-note").textContent = d.running ? "Chưa tải mô hình: ollama pull " + d.suggest : "Chưa bật Ollama nên chỉ dùng Thường";
  }).catch(() => {});

  $("chat-reset").addEventListener("click", () => {
    conv = Math.random().toString(36).slice(2, 10);     // cuộc trò chuyện mới: máy chủ tạo trạng thái khung mới
    box.innerHTML = GREETING;
    ["t-engine", "t-intent", "t-nlu", "t-state", "t-policy", "t-query", "t-conf"].forEach((id) => { $(id).textContent = "–"; });
    $("chat-results").classList.add("text-muted");
    $("chat-results").textContent = "Chưa có.";
    suggest("start");
    input.focus();
  });
  suggest("start");

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const t = input.value.trim();
    if (!t) return;
    input.value = "";
    send(t);
  });
})();
