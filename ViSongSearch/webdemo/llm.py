"""Gọi mô hình ngôn ngữ lớn chạy trên máy qua Ollama (http://127.0.0.1:11434), không cần mạng hay khóa API.

Dùng cho chế độ "LLM" của trợ lý: mô hình hiểu câu (trả về khe dạng JSON) và viết câu trả lời,
còn việc tìm bài vẫn do máy tìm kiếm của nhóm làm (kiểu RAG), nên mô hình không bịa tên bài.
Đổi mô hình: đặt biến môi trường VISONG_LLM_MODEL (mặc định qwen2.5:3b), địa chỉ: OLLAMA_HOST.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

MODEL = os.environ.get("VISONG_LLM_MODEL", "qwen2.5:3b")
HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
if not HOST.startswith("http"):
    HOST = "http://" + HOST


class Ollama:
    def __init__(self, model: str = MODEL, host: str = HOST, timeout: float = 20.0):
        self.model, self.host, self.timeout = model, host.rstrip("/"), timeout
        self._ok: tuple[float, bool, str] = (0.0, False, "")

    def status(self) -> tuple[bool, str]:
        """(chạy được không, lý do). Kết quả được nhớ 20 giây để không hỏi Ollama mỗi lượt."""
        t, ok, why = self._ok
        if time.time() - t < 20:
            return ok, why
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=2) as r:
                names = [m.get("name", "") for m in json.load(r).get("models", [])]
            if any(n == self.model or n.split(":")[0] == self.model for n in names):
                ok, why = True, ""
            else:
                ok, why = False, f"chưa tải mô hình {self.model} (gõ: ollama pull {self.model})"
        except Exception:
            ok, why = False, "chưa bật Ollama (tải tại ollama.com/download)"
        self._ok = (time.time(), ok, why)
        return ok, why

    def chat(self, system: str, user: str, as_json: bool = False, max_tokens: int = 200) -> str:
        body = {"model": self.model, "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": {"temperature": 0.1 if as_json else 0.4, "num_predict": max_tokens}}
        if as_json:
            body["format"] = "json"
        req = urllib.request.Request(self.host + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)["message"]["content"]


NLU_SYSTEM = """Bạn là bộ hiểu câu của trợ lý tìm bài hát tiếng Việt. Người dùng quên tên bài và kể những gì họ nhớ.
Đọc câu của người dùng (và câu hỏi trước đó của trợ lý nếu có), trả về DUY NHẤT một đối tượng JSON:
{"intent": một trong ["chào","cảm ơn","làm lại","không nhớ","đồng ý","xem thêm","tìm chung","cung cấp thông tin"],
 "lyric": câu hát người dùng nhớ (giữ nguyên chữ họ gõ, bỏ các chữ như "có câu", "hình như") hoặc "",
 "artist": tên ca sĩ/nhạc sĩ hoặc "",
 "mood": "vui" hoặc "buồn" hoặc "",
 "time": thời gian nhắc trong lời (mùa thu, đêm, tết, ngày xưa...) hoặc "",
 "about": chủ đề bài hát (mẹ, quê hương, chia tay...) hoặc ""}
Quy tắc: "bài khác", "không phải" = "xem thêm". "đúng rồi", "ok" = "đồng ý". "không nhớ", "của ai cũng được" = "không nhớ".
Câu chỉ xin tìm bài mà không có thông tin ("mình muốn tìm bài hát") = "tìm chung". Không tự bịa thông tin."""

NLG_SYSTEM = """Bạn là trợ lý tìm bài hát, trả lời bằng tiếng Việt, thân thiện, tối đa 2 câu ngắn.
Chỉ được nhắc tới các bài trong danh sách được đưa. Không bịa tên bài, ca sĩ hay lời bài."""
