/* Trợ lý hội thoại: gửi từng lượt nói tới /api/chat, hiện câu trả lời và trạng thái NLU / DST / policy. */
(function () {
  "use strict";
  const box = document.getElementById("chat-box");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const conv = Math.random().toString(36).slice(2, 10);
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
        body: JSON.stringify({ conv, text }),
      });
      const data = await r.json();
      wait.textContent = data.reply;
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

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const t = input.value.trim();
    if (!t) return;
    input.value = "";
    send(t);
  });
  document.querySelectorAll(".quick-reply").forEach((a) => a.addEventListener("click", () => send(a.dataset.q)));
})();
