"""Gọi mô hình ngôn ngữ lớn chạy trên máy qua Ollama (http://127.0.0.1:11434), không cần mạng hay khóa API.

Dùng cho chế độ "LLM" của trợ lý: mô hình hiểu câu (trả về khe dạng JSON) và viết câu trả lời,
còn việc tìm bài vẫn do máy tìm kiếm của nhóm làm (kiểu RAG), nên mô hình không bịa tên bài.
Mô hình: mặc định tự chọn mô hình mạnh nhất (nhiều tham số nhất) đã tải bằng "ollama pull"; trang Trợ lý
cho chọn từng mô hình. Ép một mô hình: biến môi trường VISONG_LLM_MODEL; địa chỉ Ollama: OLLAMA_HOST.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

MODEL = os.environ.get("VISONG_LLM_MODEL", "")      # để trống: tự chọn mô hình mạnh nhất đã tải
HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
KEEP_ALIVE = os.environ.get("VISONG_LLM_KEEP_ALIVE", "60m")   # giữ mô hình trong bộ nhớ, khỏi nạp lại mỗi câu
SUGGEST = "qwen2.5:7b"
if not HOST.startswith("http"):
    HOST = "http://" + HOST
NOT_CHAT = ("embed", "bge", "minilm", "rerank", "nomic", "vision", "llava")   # mô hình không dùng để trò chuyện


def size_of(name: str) -> float:
    """Số tỷ tham số đọc từ tên: "qwen2.5:7b" -> 7, "qwen2.5:1.5b-instruct" -> 1.5; không ghi thì 0."""
    m = re.search(r"(\d+(?:\.\d+)?)b(?![a-z])", name.split(":")[-1].lower())
    return float(m.group(1)) if m else 0.0


def rank_models(names: list[str]) -> list[str]:
    """Mô hình trò chuyện, mạnh nhất trước; cùng cỡ thì ưu tiên Qwen (viết tiếng Việt tốt nhất trong cỡ nhỏ)."""
    chat = [n for n in names if not any(k in n.lower() for k in NOT_CHAT)]
    return sorted(chat, key=lambda n: (-size_of(n), not n.lower().startswith("qwen"), n))


class Ollama:
    def __init__(self, model: str = MODEL, host: str = HOST, timeout: float = 45.0):
        self.model, self.host, self.timeout = model, host.rstrip("/"), timeout
        self._tags: tuple[float, list[str] | None] = (0.0, None)
        self._schema_ok = True

    def models(self) -> list[str] | None:
        """Các mô hình đã tải, mạnh nhất trước; None nếu Ollama chưa chạy. Nhớ 20 giây."""
        t, names = self._tags
        if time.time() - t < 20:
            return names
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=2) as r:
                names = rank_models([m.get("name", "") for m in json.load(r).get("models", [])])
        except Exception:
            names = None
        self._tags = (time.time(), names)
        return names

    def pick(self, want: str | None = None) -> tuple[str | None, str]:
        """(tên mô hình sẽ dùng, lý do nếu không dùng được). want: mô hình người dùng chọn trên trang."""
        names = self.models()
        if names is None:
            return None, "chưa bật Ollama (tải tại ollama.com/download)"
        w = want or self.model
        if w:
            hit = [n for n in names if n == w or n.split(":")[0] == w]
            return (hit[0], "") if hit else (None, f"chưa tải mô hình {w} (gõ: ollama pull {w})")
        if not names:
            return None, f"chưa tải mô hình nào (gõ: ollama pull {SUGGEST})"
        return names[0], ""

    def status(self) -> tuple[bool, str]:
        m, why = self.pick()
        return m is not None, why

    def chat(self, system: str, user: str, as_json: bool = False, max_tokens: int = 200,
             examples: list[tuple[str, str]] = (), schema: dict | None = None, timeout: float | None = None,
             model: str | None = None) -> str:
        """examples: các cặp (câu người dùng, câu trả lời mẫu) đặt trước câu thật (few-shot).
        schema: ép đầu ra đúng khuôn JSON (Ollama >= 0.5); bản Ollama cũ không hiểu thì tự dùng "json"."""
        msgs = [{"role": "system", "content": system}]
        for u, a in examples:
            msgs += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
        msgs.append({"role": "user", "content": user})
        body = {"model": model or self.pick()[0] or self.model, "stream": False, "messages": msgs, "keep_alive": KEEP_ALIVE,
                "options": {"temperature": 0.1 if as_json else 0.4, "num_predict": max_tokens}}
        if as_json:
            body["format"] = schema if schema and self._schema_ok else "json"
        try:
            return self._post(body, timeout)
        except urllib.error.HTTPError:
            if not isinstance(body.get("format"), dict):
                raise
            self._schema_ok = False
            body["format"] = "json"
            return self._post(body, timeout)

    def _post(self, body: dict, timeout: float | None) -> str:
        req = urllib.request.Request(self.host + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            return json.load(r)["message"]["content"]

    def warm_up(self) -> None:
        """Nạp sẵn mô hình mạnh nhất vào bộ nhớ (chạy nền khi mở web), để câu hỏi đầu tiên không phải chờ."""
        def run():
            try:
                m = self.pick()[0]
                if m:
                    self.chat("Trả lời: ok", "ok", max_tokens=1, timeout=180, model=m)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()


NLU_SYSTEM = """Bạn là bộ hiểu câu của trợ lý tìm bài hát tiếng Việt. Người dùng quên tên bài và kể những gì họ nhớ.
Đọc câu của người dùng (và câu hỏi trước đó của trợ lý nếu có), trả về DUY NHẤT một đối tượng JSON:
{"intent": một trong ["chào","cảm ơn","làm lại","không nhớ","đồng ý","xem thêm","tìm chung","hỏi về bài","cung cấp thông tin"],
 "lyric": câu hát người dùng nhớ (giữ nguyên chữ họ gõ, bỏ các chữ như "có câu", "hình như") hoặc "",
 "artist": tên ca sĩ/nhạc sĩ hoặc "",
 "mood": "vui" hoặc "buồn" hoặc "",
 "time": thời gian nhắc trong lời (mùa thu, đêm, tết, ngày xưa...) hoặc "",
 "about": chủ đề bài hát (mẹ, quê hương, chia tay...) hoặc ""}
Quy tắc: "bài khác", "không phải" = "xem thêm". "đúng rồi", "ok" = "đồng ý". "không nhớ", "của ai cũng được" = "không nhớ".
Câu chỉ xin tìm bài mà không có thông tin ("mình muốn tìm bài hát") = "tìm chung".
Hỏi về bài vừa được đưa ("bài này của ai", "lời thế nào", "ra năm nào") = "hỏi về bài".
Người dùng gõ không dấu thì giữ nguyên chữ họ gõ. Không tự bịa thông tin."""

NLG_SYSTEM = """Bạn là trợ lý tìm bài hát, trả lời bằng tiếng Việt, thân thiện, tối đa 2 câu ngắn.
Chỉ được nhắc tới các bài trong danh sách được đưa. Không bịa tên bài, ca sĩ hay lời bài."""

INTENT_NAMES = ["chào", "cảm ơn", "làm lại", "không nhớ", "đồng ý", "xem thêm", "tìm chung", "hỏi về bài",
                "cung cấp thông tin"]
# Khuôn JSON cho Ollama >= 0.5: mô hình chỉ được chọn ý định trong danh sách, không thể trả sai dạng
NLU_SCHEMA = {"type": "object", "required": ["intent", "lyric", "artist", "mood", "time", "about"],
              "properties": {"intent": {"type": "string", "enum": INTENT_NAMES},
                             **{k: {"type": "string"} for k in ("lyric", "artist", "mood", "time", "about")}}}


def _j(intent, lyric="", artist="", mood="", time="", about=""):
    return json.dumps({"intent": intent, "lyric": lyric, "artist": artist, "mood": mood, "time": time,
                       "about": about}, ensure_ascii=False)


# Ví dụ mẫu (few-shot) đặt trước câu thật: mô hình nhỏ bắt chước rất tốt, nhất là câu nói vòng, không dấu
NLU_EXAMPLES = [
    ("Người dùng: mình muốn tìm một bài hát", _j("tìm chung")),
    ("Người dùng: hình như có câu em của ngày hôm qua", _j("cung cấp thông tin", lyric="em của ngày hôm qua")),
    ("Người dùng: bai hat co cau nang am xa dan cua son tung", _j("cung cấp thông tin", lyric="nang am xa dan",
                                                                    artist="son tung")),
    ("Người dùng: bài gì buồn buồn nói về người mẹ tảo tần nuôi con, hình như ca sĩ là phạm hồng phước",
     _j("cung cấp thông tin", artist="phạm hồng phước", mood="buồn", about="người mẹ tảo tần nuôi con")),
    ("Trợ lý vừa hỏi: Bạn có nhớ ca sĩ hoặc nhạc sĩ không?\nNgười dùng: của ai cũng được", _j("không nhớ")),
    ("Trợ lý vừa hỏi: Bài hát vui hay buồn?\nNgười dùng: chắc là buồn, lời có nhắc mùa thu",
     _j("cung cấp thông tin", mood="buồn", time="mùa thu")),
    ("Trợ lý vừa đưa bài: Nơi Này Có Anh - Sơn Tùng M-TP\nNgười dùng: không phải bài đó", _j("xem thêm")),
    ("Trợ lý vừa đưa bài: Nơi Này Có Anh - Sơn Tùng M-TP\nNgười dùng: chuẩn luôn", _j("đồng ý")),
    ("Trợ lý vừa đưa bài: Nơi Này Có Anh - Sơn Tùng M-TP\nNgười dùng: bài này ra năm nào vậy", _j("hỏi về bài")),
    ("Người dùng: bài kể về chàng trai đứng chờ người yêu dưới mưa mà cô ấy không đến",
     _j("cung cấp thông tin", about="chàng trai chờ người yêu dưới mưa không đến")),
]

QA_SYSTEM = """Bạn là trợ lý tìm bài hát. Trả lời câu hỏi của người dùng về bài hát CHỈ dựa trên thông tin được đưa
(tên bài, nghệ sĩ, album, lời). Thông tin không có thì nói là trong dữ liệu không có. Tối đa 3 câu, tiếng Việt."""
