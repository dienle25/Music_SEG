"""Build the human-query questionnaire (one Google Form) for the test songs.

Each test-split song cluster becomes one optional short-answer question that shows
only the title and artist (never lyrics). Participants answer the songs they know
by typing what they would type into a search box if they had forgotten the title.

Outputs (in OUT_DIR):
  human_prompts.json   prompt id -> cluster, representative doc, shown title/artist
  create_form.gs       Google Apps Script that creates the form in one run
  form_questions.md    the same questions as text, for review or manual entry

    python3 scripts/make_human_form.py work_pilot docs/human_queries [max_songs]

With max_songs, only that many test songs are asked about, the most viewed first (people can
only answer songs they know; a form with thousands of questions is not answerable).
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from visong.corpus import is_compilation_song, load_corpus, singers_of      # noqa: E402
from visong.dedup import norm_title                        # noqa: E402

TITLE_NOISE = re.compile(r"\s*[\(\[][^)\]]*(remix|lofi|cover|version|soundtrack|ost|live|ca si|beat|ver)[^)\]]*[\)\]]",
                         re.I)
SKIP_TITLES = re.compile(r"(?i)^(demo\b|track\s*\d+|saigon1$)")
FORM_TITLE = "ViSongSearch: bạn tìm bài hát thế nào khi quên tên?"
FORM_INTRO = (
    "Khảo sát này thu cách mọi người gõ tìm một bài hát khi không nhớ tên bài, cho một nghiên cứu "
    "về máy tìm kiếm bài hát tiếng Việt (môn SEG301 và TMG301, Đại học FPT). Khảo sát ẩn danh, không "
    "hỏi tên hay email, mất khoảng 5–10 phút. Câu trả lời chỉ dùng để đánh giá hệ thống tìm kiếm và có thể "
    "được công bố ở dạng ẩn danh cùng bài báo. Bạn có thể dừng bất cứ lúc nào.\n\n"
    "Cách làm: mỗi câu hỏi là một bài hát (tên và ca sĩ). Với bài bạn biết, hãy tưởng tượng bạn quên mất "
    "tên bài và gõ đúng như bạn sẽ gõ vào ô tìm kiếm: một câu lời bạn nhớ (nhớ sai cũng được), hoặc mô tả "
    "bài hát. Không gõ tên bài, không tra lời trên mạng. Bỏ trống những bài bạn không biết. "
    "Hãy trả lời ít nhất 5 bài.")
SONG_HELP = ("Gõ như khi tìm kiếm: lời bạn nhớ hoặc mô tả bài. Không gõ tên bài. "
             "Gõ có dấu hay không dấu tùy thói quen của bạn. Bỏ trống nếu không biết bài.")


# songs whose indexed lyrics are badly transcribed and have no clean version in the corpus
GARBLED = {"bui sao", "ho", "tinh", "hoa no ben duong"}


def shown_title(title: str) -> str:
    t = TITLE_NOISE.sub("", title).strip()
    if t.isupper():
        return t.title()
    return t[:1].upper() + t[1:] if t.islower() else t


def shown_artist(artist: str) -> str:
    parts = [p.strip() for p in artist.split(",") if p.strip()]
    show = [p for p in parts if '"' not in p][:3] or parts[:1]
    return ", ".join(show)


def main(work="work_pilot", out_dir="docs/human_queries", max_songs=None, split="test"):
    songs = load_corpus(os.path.join(work, "corpus.jsonl"))
    by_cluster = defaultdict(list)
    for s in songs:
        if s.extra["split"] == split:
            by_cluster[s.extra["cluster"]].append(s)
    prompts = []
    for cluster, members in sorted(by_cluster.items()):
        plain = [m for m in members if not is_compilation_song(m)]
        if not plain:
            continue
        titles = Counter(shown_title(m.title) for m in plain)
        title = sorted(titles.items(), key=lambda x: (-x[1], len(x[0])))[0][0]
        rep = max(plain, key=lambda m: len(m.lyrics))
        # performers are what people remember; Hợp Âm Chuẩn's `artist` is the composer
        artist = shown_artist(", ".join(singers_of(rep)[:2]) or rep.artist)
        if SKIP_TITLES.search(title) or not artist or norm_title(title) in GARBLED:
            continue
        prompts.append({"cluster": cluster, "doc_id": rep.doc_id, "title": title, "artist": artist,
                        "source": rep.source, "views": max(m.extra.get("views") or 0 for m in plain)})
    if max_songs:
        prompts = sorted(prompts, key=lambda p: (-p["views"], p["cluster"]))[:int(max_songs)]
    prompts.sort(key=lambda p: (p["artist"].lower(), p["title"].lower()))
    for i, p in enumerate(prompts, 1):
        p["pid"] = f"S{i:02d}"
    os.makedirs(out_dir, exist_ok=True)
    json.dump(prompts, open(os.path.join(out_dir, "human_prompts.json"), "w"), ensure_ascii=False, indent=1)

    items = ",\n".join(f"    [{json.dumps(p['pid'])}, {json.dumps(p['title'], ensure_ascii=False)}, "
                       f"{json.dumps(p['artist'], ensure_ascii=False)}]" for p in prompts)
    gs = f"""/**
 * Creates the ViSongSearch human-query form. Open https://script.google.com, New project,
 * paste this file, press Run (choose createForm), allow access. The form's links appear
 * in the execution log (View > Logs).
 */
var SONGS = [
{items}
];

function createForm() {{
  var form = FormApp.create({json.dumps(FORM_TITLE, ensure_ascii=False)});
  form.setDescription({json.dumps(FORM_INTRO, ensure_ascii=False)});
  form.setCollectEmail(false);
  form.setShuffleQuestions(true);
  form.setProgressBar(false);
  form.setConfirmationMessage("Cảm ơn bạn! Câu trả lời đã được ghi lại.");
  form.addMultipleChoiceItem()
      .setTitle("Khi tìm kiếm bằng tiếng Việt, bạn thường gõ:")
      .setChoiceValues(["Có dấu đầy đủ", "Không dấu", "Lúc có dấu, lúc không"])
      .setRequired(true);
  form.addMultipleChoiceItem()
      .setTitle("Bạn đang làm khảo sát trên:")
      .setChoiceValues(["Điện thoại", "Máy tính"])
      .setRequired(true);
  SONGS.forEach(function (s) {{
    form.addTextItem()
        .setTitle(s[1] + " (" + s[2] + ") #" + s[0])
        .setHelpText({json.dumps(SONG_HELP, ensure_ascii=False)})
        .setRequired(false);
  }});
  Logger.log("Link to share: " + form.getPublishedUrl());
  Logger.log("Edit link: " + form.getEditUrl());
}}
"""
    open(os.path.join(out_dir, "create_form.gs"), "w").write(gs)
    md = [f"# {FORM_TITLE}", "", FORM_INTRO, "", "1. Khi tìm kiếm bằng tiếng Việt, bạn thường gõ: Có dấu đầy đủ / Không dấu / "
          "Lúc có dấu, lúc không (bắt buộc)", "2. Bạn đang làm khảo sát trên: Điện thoại / Máy tính (bắt buộc)", ""]
    md += [f"- {p['title']} ({p['artist']}) #{p['pid']}" for p in prompts]
    md += ["", f"Mỗi bài là câu hỏi trả lời ngắn, không bắt buộc, gợi ý: {SONG_HELP}",
           "Thứ tự câu hỏi được xáo trộn cho từng người (Settings → Presentation → Shuffle question order)."]
    open(os.path.join(out_dir, "form_questions.md"), "w").write("\n".join(md) + "\n")
    print(f"{len(prompts)} songs -> {out_dir}")


if __name__ == "__main__":
    main(*sys.argv[1:])
