"""Trợ lý tìm bài hát theo khung (frame-based dialogue system, TMG 10 Chatbot and Dialogue Systems).

Các thành phần như trong slide:
- NLU (hiểu ngôn ngữ): nhận ý định (chào, cảm ơn, đồng ý, phủ định, xem thêm, làm lại, không nhớ, cung cấp
  thông tin) bằng mẫu, và điền khe (ca sĩ, lời, tâm trạng, thời gian, chủ đề) bằng văn phạm truy vấn
- DST (theo dõi trạng thái hội thoại): gộp các khe qua nhiều lượt nói
- Chính sách hội thoại (dialogue policy): đủ tự tin thì đưa ra bài, chưa chắc thì hỏi thêm khe còn thiếu
- NLG (sinh ngôn ngữ): câu trả lời theo mẫu tiếng Việt
"""
from __future__ import annotations

import json
import re
import time

from visong.text import clean, remove_diacritics

from . import llm as llm_mod
from . import temporal

INTENTS = [
    ("chào", r"^(xin chào|chào|hello|hi|alo|hey)\b"),
    ("cảm ơn", r"(cảm ơn|cám ơn|thank|thanks|tks)"),
    ("làm lại", r"(làm lại|bắt đầu lại|reset|xóa hết)"),
    ("không nhớ", r"(không nhớ|quên rồi|không biết|chịu|hổng nhớ|ko nhớ)"),
    ("đồng ý", r"^(đúng rồi|đúng|chính nó|phải rồi|ok|oke|ừ|uh|đúng bài|chuẩn|yes)\b"),
    ("xem thêm", r"(không phải|sai rồi|chưa đúng|bài khác|còn bài nào|xem thêm|tiếp|bài nào nữa)"),
    ("hỏi về bài", r"^(bài (này|đó|đấy|đấy là) )?(của ai|ai hát|ai sáng tác|tên (là )?gì)( (vậy|thế|nhỉ|đấy|ạ))*$"),
]
# Cụm yêu cầu chung ở đầu câu ("mình muốn tìm bài hát", "bài gì mà"...): bỏ đi trước khi hiểu câu,
# để không bị coi là lời bài hát. Không bỏ đại từ đứng một mình ("em của ngày hôm qua" vẫn là lời).
REQUEST_RX = re.compile(
    r"^(?:(?:mình|tôi|tui|em|anh|bạn)\s+)?(?:(?:muốn|cần|đang|định)\s+)*(?:tìm|kiếm|nghe)"
    r"(?:\s+(?:giúp|giùm|hộ|dùm))?(?:\s+(?:mình|tôi|tui|em))?(?:\s+(?:lại|một|cái))*"
    r"(?:\s+(?:bài hát|bài nhạc|bài|nhạc))?(?:\s+(?:nào|gì))?\b"
    r"|^cho\s+(?:mình|tôi|tui|em)\s+(?:xin\s+|nghe\s+)?(?:một\s+)?(?:bài hát|bài nhạc|bài|nhạc)\b"
    r"|^(?:bài|bài hát|bài nhạc)\s+(?:gì|nào)\s+mà\b"
    r"|^(?:mình|tôi|tui|em|anh)\s+nhớ\s+(?:là\s+)?(?:có\s+)?(?:một\s+)?(?:bài|bài hát)\b")
# Câu chỉ còn từ chung chung, không có thông tin gì để tìm
GENERIC = re.compile(r"^(?:bài hát|bài nhạc|bài|nhạc|nào|gì|hay|có|không|ko|vậy|nhé|nha|đi|với|giúp|mình|tôi|ạ|ơi|\s)*$")
# "của ai cũng được": người dùng không nhớ / không cần ca sĩ
ANY_ARTIST = re.compile(r"(?:của\s+)?(?:ai hát|ca sĩ nào|ai)\s+cũng\s+được")
QUESTIONS = {
    "lyric": "Bạn còn nhớ câu hát nào không, dù chỉ vài chữ hay nhớ sai cũng được?",
    "artist": "Bạn có nhớ ca sĩ hoặc nhạc sĩ không?",
    "mood": "Bài hát vui hay buồn?",
    "time": "Lời bài hát có nhắc thời gian nào không (mùa thu, đêm, tết, ngày xưa...)?",
}
SLOT_NAMES = {"artist": "ca sĩ", "lyric": "lời", "mood": "tâm trạng", "time": "thời gian", "about": "chủ đề"}
# từ đệm / từ chức năng được bỏ qua khi xét câu trả lời ngắn chỉ gồm tên khe ("buồn lắm", "của mỹ tâm")
FILLER_KINDS = {"N_SONG", "N_SINGER", "P_BY", "P_ABOUT", "P_IN", "ADV", "V_SING", "V_REQ", "HEDGE"}
FILLER_WORDS = {"lắm", "quá", "à", "ạ", "nha", "nhé", "thôi", "đó", "hơi", "rất", "khá", "ấy", "nhỉ"}


def plain(s: str) -> str:
    return remove_diacritics(clean(s))


class DialogManager:
    def __init__(self, engine):
        self.eng = engine
        self.sessions: dict[str, dict] = {}
        self.llm = llm_mod.Ollama()

    def new_state(self):
        return {"slots": {}, "asked": [], "offset": 0, "last_act": None, "last_results": [], "turns": 0}

    # ------------------------------------------------------------ NLU
    @staticmethod
    def slot_words(chunks) -> dict | None:
        """Câu trả lời ngắn chỉ gồm tên ca sĩ, tâm trạng, thời gian (và từ đệm) thì trả về các khe đó."""
        out = {}
        for c in chunks:
            if c["kind"] == "MOOD":
                out["mood"] = c["value"]
            elif c["kind"] == "TIME":
                tags = temporal.tag(c["text"])
                out["time"] = {"text": c["text"], "value": tags[0]["value"] if tags else "",
                               "name": temporal.TIME_TAG_NAMES.get(tags[0]["value"], c["text"]) if tags else c["text"]}
            elif c["kind"] == "ARTIST":
                out["artist"], out["artist_text"] = c["value"], c["text"]
            elif not (c["kind"] in FILLER_KINDS or c["text"] in FILLER_WORDS):
                return None
        return out or None

    def nlu(self, text: str, st: dict) -> dict:
        t = clean(text)
        intent = "cung cấp thông tin"
        for name, rx in INTENTS:
            src = t if re.search(rx, t) else plain(text)
            m = re.search(rx, src)
            if m:
                intent = name
                if name == "xem thêm":     # bỏ cụm "bài khác", "không phải"... để phần còn lại (nếu có) là thông tin mới
                    toks = t.split()
                    t = " ".join(toks[:len(src[:m.start()].split())] + toks[len(src[:m.end()].split()):])
                break
        slots = {}
        if intent == "cung cấp thông tin" and ANY_ARTIST.search(t):
            if "artist" not in st["asked"]:
                st["asked"].append("artist")
            t = ANY_ARTIST.sub(" ", t).strip()
            if not t or GENERIC.match(t):
                intent = "không nhớ"
        if intent in ("cung cấp thông tin", "xem thêm") and t:
            t = REQUEST_RX.sub(" ", t).strip()
            if GENERIC.match(t):
                t = ""
                if intent == "cung cấp thông tin":
                    intent = "tìm chung"
        if intent in ("cung cấp thông tin", "xem thêm") and t:
            u = self.eng.grammar.understand(t)
            s = u["slots"]
            asked = st.get("last_act", "") or ""
            direct = self.slot_words(u["chunks"])
            if direct:              # "buồn", "mùa thu", "noo phuoc thinh": điền đúng khe đó dù vừa hỏi khe khác
                slots.update(direct)
            elif s.get("intent") == "lyric" and asked.startswith("hỏi_"):
                want = asked[4:]
                if want == "artist":
                    ents = [e for e in self.eng.gazetteer.find(t) if e["type"] == "CA_SĨ"]
                    slots["artist"] = ents[0]["value"] if ents else text.strip()
                elif want == "mood":
                    m = self.eng.grammar.moods
                    hit = [v for k, v in m.items() if plain(k) in plain(t)]
                    slots["mood"] = hit[0] if hit else None
                elif want == "time":
                    tags = temporal.tag(t)
                    if tags:
                        slots["time"] = {"text": tags[0]["text"], "value": tags[0]["value"],
                                         "name": temporal.TIME_TAG_NAMES.get(tags[0]["value"], tags[0]["text"])}
                else:
                    slots["lyric"] = t
            else:
                for k in ("artist", "lyric", "mood", "time", "about"):
                    if s.get(k):
                        slots[k] = s[k]
                if s.get("artist_text"):
                    slots["artist_text"] = s["artist_text"]
                if not slots.get("artist"):      # ca sĩ nằm giữa câu: "bài sơn tùng hát về ...", "ca sĩ là mỹ tâm"
                    ents = [e for e in self.eng.gazetteer.find(t) if e["type"] == "CA_SĨ"]
                    if ents:
                        e = ents[0]
                        slots["artist"] = e["value"]
                        toks = t.split()
                        i, j = e["span"] if e.get("span") else (0, 0)
                        left, right = toks[:i], toks[j:]
                        while left and left[-1] in ("là", "của", "do", "bài", "sĩ", "ca", "nhạc"):
                            left = left[:-1]
                        while right and right[0] in ("hát", "trình", "bày", "thể", "hiện"):
                            right = right[1:]
                        rest = " ".join(left + right)
                        if slots.get("lyric"):
                            slots["lyric"] = rest if not GENERIC.match(rest) else None
            if intent == "xem thêm" and not slots:
                pass
            elif intent == "xem thêm" and slots:
                intent = "cung cấp thông tin"
        return {"intent": intent, "slots": {k: v for k, v in slots.items() if v}}

    def nlu_llm(self, text: str, st: dict) -> dict:
        """NLU bằng LLM: mô hình trả JSON, rồi chuẩn hóa về đúng dạng khe của hệ luật (tên ca sĩ, nhãn thời gian)."""
        asked = st.get("last_act") or ""
        q = QUESTIONS.get(asked[4:], "") if asked.startswith("hỏi_") else ""
        ctx = f"Trợ lý vừa hỏi: {q}\n" if q else ""
        if asked in ("đưa_kết_quả", "đưa_thêm", "xác_nhận") and st["last_results"]:   # ngữ cảnh: bài vừa đưa
            ctx = "Trợ lý vừa đưa bài: " + "; ".join(f"{h['title']} - {h['artist']}" for h in st["last_results"][:3]) + "\n"
        raw = self.llm.chat(llm_mod.NLU_SYSTEM, ctx + f"Người dùng: {text}", as_json=True, max_tokens=160,
                            examples=llm_mod.NLU_EXAMPLES, schema=llm_mod.NLU_SCHEMA)
        d = json.loads(raw)
        intent = d.get("intent") or "cung cấp thông tin"
        if intent not in llm_mod.INTENT_NAMES:
            intent = "cung cấp thông tin"
        slots = {}
        if (d.get("lyric") or "").strip():
            slots["lyric"] = clean(d["lyric"])
        if (d.get("artist") or "").strip():
            ents = [e for e in self.eng.gazetteer.find(d["artist"]) if e["type"] == "CA_SĨ"]
            slots["artist"] = ents[0]["value"] if ents else d["artist"].strip()
        if (d.get("mood") or "").strip():
            m = plain(d["mood"])
            hit = [v for k, v in self.eng.grammar.moods.items() if plain(k) == m]
            slots["mood"] = hit[0] if hit else d["mood"].strip()
        if (d.get("time") or "").strip():
            tags = temporal.tag(clean(d["time"]))
            slots["time"] = {"text": clean(d["time"]), "value": tags[0]["value"] if tags else "",
                             "name": temporal.TIME_TAG_NAMES.get(tags[0]["value"], d["time"]) if tags else d["time"]}
        if (d.get("about") or "").strip():
            slots["about"] = clean(d["about"])
        if intent in ("xem thêm", "tìm chung") and slots:
            intent = "cung cấp thông tin"
        if intent == "hỏi về bài":
            slots = {}
        return {"intent": intent, "slots": slots, "by": "llm"}

    def _answer_about(self, text: str, hit: dict, engine: str) -> str:
        """Trả lời câu hỏi về bài vừa đưa, chỉ dựa trên dữ liệu của bài (RAG); chế độ luật trả lời theo mẫu."""
        song = self.eng.by_id.get(hit["objectID"])
        album = (song.extra.get("album", "") if song else "") or ""
        if engine == "llm" and song:
            lines = " / ".join(song.lines()[:8])
            info = f"Tên bài: {song.title}\nNghệ sĩ: {song.artist}\nAlbum: {album or 'không rõ'}\nĐoạn đầu lời: {lines}"
            return self.llm.chat(llm_mod.QA_SYSTEM, f"{info}\n\nCâu hỏi: {text}", max_tokens=150).strip()
        return f"Bài “{hit['title']}” do {hit['artist']} thể hiện" + (f", trong album {album}" if album else "") + "."

    def _nlg_llm(self, text: str, r: dict) -> str:
        """NLG bằng LLM, chỉ dựa trên các bài máy tìm kiếm vừa trả về (RAG)."""
        songs = "\n".join(f"{i + 1}. {h['title']} - {h['artist']}" for i, h in enumerate(r["results"]))
        act = r["policy"]
        goal = {"đưa_kết_quả": "Giới thiệu bài số 1 là bài có khả năng đúng nhất và hỏi người dùng đã đúng bài chưa.",
                "đưa_thêm": "Giới thiệu các bài trong danh sách và hỏi có bài nào đúng không.",
                "xác_nhận": "Chúc mừng người dùng đã tìm được bài số 1."}.get(act)
        if goal is None:
            goal = "Nói rằng đã có vài bài gần đúng nhưng chưa chắc, rồi hỏi đúng câu này: " + \
                   QUESTIONS.get(act[4:], "Bạn kể thêm chi tiết nhé?")
        return self.llm.chat(llm_mod.NLG_SYSTEM, f"Người dùng vừa nói: {text}\nDanh sách bài:\n{songs}\nNhiệm vụ: {goal}",
                             max_tokens=120).strip()

    # ------------------------------------------------------------ truy vấn từ khung
    @staticmethod
    def frame_query(slots: dict) -> str:
        parts = ["bài hát"]
        if slots.get("mood"):
            parts.append(slots["mood"])
        if slots.get("about"):
            parts.append("về " + slots["about"])
        if slots.get("time") and not slots.get("about"):
            parts.append("vào " + slots["time"]["text"])
        if slots.get("artist"):
            parts.append("của " + slots["artist"])
        if slots.get("lyric"):
            parts.append("có câu " + slots["lyric"])
        return " ".join(parts) if len(parts) > 1 else ""

    # ------------------------------------------------------------ một lượt
    def turn(self, session: str, text: str, mode: str = "rule") -> dict:
        """mode="llm": hiểu câu và viết câu trả lời bằng mô hình Ollama; lỗi hoặc chưa bật thì tự dùng luật."""
        t0 = time.perf_counter()
        engine, note = "luật", ""
        if mode == "llm":
            ok, why = self.llm.status()
            if ok:
                engine = "llm"
            else:
                note = why
        try:
            r = self._turn(session, text, engine)
        except Exception as e:          # mô hình lỗi giữa chừng: làm lại lượt này bằng luật
            if engine != "llm":
                raise
            st = self.sessions.get(session)
            if st:
                st["turns"] -= 1
            engine, note = "luật", f"LLM lỗi ({type(e).__name__}), dùng luật"
            r = self._turn(session, text, engine)
        if engine == "llm" and r.get("results") and r["policy"] in ("đưa_kết_quả", "đưa_thêm", "xác_nhận") or \
                engine == "llm" and r["policy"].startswith("hỏi_") and r.get("results"):
            try:
                r["reply"] = self._nlg_llm(text, r) or r["reply"]
            except Exception:
                note = "LLM viết câu lỗi, dùng câu mẫu"
        r["engine"] = (f"LLM ({self.llm.model}) · {time.perf_counter() - t0:.1f} giây" if engine == "llm" else "luật") + \
            (f" · {note}" if note else "")
        return r

    def _turn(self, session: str, text: str, engine: str) -> dict:
        st = self.sessions.setdefault(session, self.new_state())
        st["turns"] += 1
        nlu = self.nlu_llm(text, st) if engine == "llm" else self.nlu(text, st)
        intent = nlu["intent"]
        if intent == "làm lại":
            self.sessions[session] = st = self.new_state()
            return self._reply(st, nlu, "đặt_lại", "Mình đã xóa hết thông tin. " + QUESTIONS["lyric"])
        if intent == "chào" and not nlu["slots"]:
            return self._reply(st, nlu, "chào", "Chào bạn! Mình giúp bạn tìm bài hát khi quên tên. " + QUESTIONS["lyric"])
        if intent == "cảm ơn":
            return self._reply(st, nlu, "kết_thúc", "Không có gì! Khi nào cần tìm bài khác cứ gõ cho mình nhé.")
        if intent == "đồng ý" and not st["last_results"]:
            st["last_act"] = "hỏi_lyric"
            return self._reply(st, nlu, "hỏi_lyric", QUESTIONS["lyric"])
        if intent == "đồng ý" and st["last_results"]:
            top = st["last_results"][0]
            sim = self.eng.similar(top["objectID"], 3)
            names = ", ".join(self.eng.by_id[d].title for d, _ in sim)
            return self._reply(st, nlu, "xác_nhận",
                               f"Tuyệt! Bài bạn tìm là “{top['title']}” ({top['artist']}). Có thể bạn cũng thích: {names}.",
                               [top])
        if intent == "hỏi về bài":
            if not st["last_results"]:
                st["last_act"] = "hỏi_lyric"
                return self._reply(st, nlu, "hỏi_lyric", "Mình chưa đưa bài nào. " + QUESTIONS["lyric"])
            act = st["last_act"]
            r = self._reply(st, nlu, "trả_lời", self._answer_about(text, st["last_results"][0], engine),
                            st["last_results"][:1])
            st["last_act"] = act            # giữ ngữ cảnh: người dùng vẫn có thể nói "đúng rồi" / "bài khác"
            return r
        if intent == "tìm chung":
            if st["slots"]:
                return self._search_and_reply(st, nlu)
            st["last_act"] = "hỏi_lyric"
            return self._reply(st, nlu, "hỏi_lyric", "Được, mình giúp bạn. " + QUESTIONS["lyric"])
        if intent == "không nhớ":
            if st["last_act"] and st["last_act"].startswith("hỏi_"):
                st["asked"].append(st["last_act"][4:])
            return self._search_and_reply(st, nlu, note="Không sao. ")
        if intent == "xem thêm" and not nlu["slots"]:
            st["offset"] += 3
            return self._search_and_reply(st, nlu, note="Mình đưa thêm vài bài khác nhé. ", more=True)
        # cung cấp thông tin: cập nhật trạng thái (DST)
        for k, v in nlu["slots"].items():
            if k == "lyric" and st["slots"].get("lyric") and v not in st["slots"]["lyric"]:
                st["slots"]["lyric"] = st["slots"]["lyric"] + " " + v
            else:
                st["slots"][k] = v
        st["offset"] = 0
        if not st["slots"]:
            st["slots"]["lyric"] = clean(text)
        return self._search_and_reply(st, nlu)

    def _search_and_reply(self, st, nlu, note: str = "", more: bool = False) -> dict:
        q = self.frame_query(st["slots"])
        if not q:
            st["last_act"] = "hỏi_lyric"
            return self._reply(st, nlu, "hỏi_lyric", note + QUESTIONS["lyric"])
        res = self.eng.search(q, hits_per_page=3, page=st["offset"] // 3)
        hits = res["hits"]
        st["last_results"] = hits
        scores = [h["score"] for h in hits]
        conf = (scores[0] - scores[1]) / scores[0] if len(scores) > 1 and scores[0] > 0 else (1.0 if scores else 0)
        missing = [k for k in ("lyric", "artist", "mood", "time") if not st["slots"].get(k) and k not in st["asked"]]
        if not hits:
            act = f"hỏi_{missing[0]}" if missing else "không_thấy"
            text = note + "Mình chưa tìm thấy bài nào khớp. " + (QUESTIONS[missing[0]] if missing else
                                                                  "Bạn thử tả lại theo cách khác nhé.")
            return self._reply(st, nlu, act, text, [], q, conf)
        known = ", ".join(f"{SLOT_NAMES[k]}: {v['name'] if isinstance(v, dict) else v}"
                          for k, v in st["slots"].items() if k in SLOT_NAMES)
        if more:
            act = "đưa_thêm"
            names = "; ".join(f"“{h['title']}” ({h['artist']})" for h in hits)
            return self._reply(st, nlu, act, note + f"{names}. Có bài nào đúng không?", hits, q, conf)
        if conf >= 0.35 or not missing or self._exact(st["slots"], hits[0]):
            act = "đưa_kết_quả"
            text = (note + f"Với thông tin ({known}), mình nghĩ đó là “{hits[0]['title']}” của {hits[0]['artist']}. "
                    "Đúng bài chưa? Nếu chưa, gõ “bài khác” hoặc kể thêm chi tiết.")
        else:
            act = f"hỏi_{missing[0]}"
            st["asked"].append(missing[0])
            text = (note + f"Mình tìm được vài bài gần đúng ({known}) nhưng chưa chắc. " + QUESTIONS[missing[0]])
        return self._reply(st, nlu, act, text, hits, q, conf)

    def _exact(self, slots: dict, hit: dict) -> bool:
        """Lời người dùng nhớ trùng gần nguyên văn với tên bài hoặc một đoạn lời của bài đứng đầu."""
        lyric = plain(slots.get("lyric") or "")
        if len(lyric.split()) < 2:
            return False
        song = self.eng.by_id.get(hit["objectID"])
        title = plain(hit.get("title") or "")
        body = plain(" ".join((getattr(song, "lyrics", "") or "").split())) if song else ""
        return bool(title) and (title in lyric or lyric in title) or (len(lyric.split()) >= 4 and lyric in body)

    def _reply(self, st, nlu, act, text, hits=None, query="", conf=None) -> dict:
        st["last_act"] = act
        return {"reply": text, "results": [{"objectID": h["objectID"], "title": h["title"], "artist": h["artist"],
                                             "page": h["page"]} for h in (hits or [])],
                "nlu": nlu, "state": {k: v for k, v in st["slots"].items()}, "policy": act,
                "query": query, "confidence": None if conf is None else round(conf, 3)}
