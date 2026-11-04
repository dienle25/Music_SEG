from visong.corpus import Song, clean_lyrics, is_compilation, passages_of
from visong.dedup import cluster_songs, norm_title


def test_clean_lyrics_removes_page_chrome_and_markers():
    raw = "\n".join([
        "Đăng bởicool", "Bài hát: Chạy Ngay Đi", "Ca sỹ: Sơn Tùng M-TP",
        "[Am]Từng phút cứ mãi [C]trôi xa", "ĐK:", "ĐK: Dưới ánh nắng sương",
        "Σhỉ còn Ɲhìn em", "x4535x", "Theo dõi em từng bước chân", "Vẫn nghe hơi ấm. Xem hết",
        "Xem thêm", "Nghệ sĩ", "Dương Domic", "Theo dõi"])
    out = clean_lyrics(raw).split("\n")
    assert out == ["Từng phút cứ mãi trôi xa", "Dưới ánh nắng sương", "Chỉ còn Nhìn em",
                   "Theo dõi em từng bước chân", "Vẫn nghe hơi ấm."]


def test_clean_lyrics_keeps_stanza_breaks_and_russian():
    out = clean_lyrics("dòng một\n\n\ndòng hai\nМиллион алых роз")
    assert out == "dòng một\n\ndòng hai\nМиллион алых роз"


def test_compilation_titles():
    assert is_compilation("LK Nhạc Xuân") and is_compilation("Mashup Em Gái Mưa")
    assert not is_compilation("Em Gái Mưa")


def test_passages_overlap():
    song = Song("d1", "u", "s", "t", lyrics="\n".join(f"dòng số {i} có vài chữ" for i in range(10)))
    ps = passages_of(song, window=4, stride=2)
    assert ps[0]["pid"] == "d1#0" and ps[0]["line_start"] == 0
    assert ps[1]["line_start"] == 2
    assert all(len(p["text"].split("\n")) <= 4 for p in ps)


def test_cluster_songs_groups_versions():
    lyric = " ".join(f"chữ{i}" for i in range(80))
    songs = [Song("a", "u", "s", "Em Gái Mưa", lyrics=lyric),
             Song("b", "u", "s", "Em Gái Mưa (Remix)", lyrics=lyric + " thêm"),
             Song("c", "u", "s", "Bài Khác", lyrics=" ".join(f"từ{i}" for i in range(80)))]
    cluster_of, stats = cluster_songs(songs)
    assert cluster_of["a"] == cluster_of["b"] != cluster_of["c"]
    assert norm_title("Em Gái Mưa (Remix)") == norm_title("Em gái mưa")


def words(prefix, n):
    return " ".join(f"{prefix}{i}" for i in range(n))


def test_cluster_songs_checks_the_exact_jaccard():
    base = words("chữ", 100)                       # 96 shingles
    # 60 of 100 words shared -> about 0.4 Jaccard: below 0.5, never merged whatever MinHash says
    near = " ".join(base.split()[:60] + words("khác", 40).split())
    songs = [Song("a", "u", "s", "Một", lyrics=base), Song("b", "u", "s", "Hai", lyrics=base + " thêm vài chữ"),
             Song("c", "u", "s", "Ba", lyrics=near)]
    cluster_of, stats = cluster_songs(songs)
    assert cluster_of["a"] == cluster_of["b"] != cluster_of["c"]
    assert stats["lsh_rows"] == 3 and stats["p_candidate_at_threshold"] > 0.99


def test_find_compilations_needs_title_parts_and_borrowed_lyrics():
    from visong.dedup import find_compilations
    a, b = words("mưa", 60), words("nắng", 60)
    songs = [Song("a", "u", "s", "Mưa Rơi", lyrics=a), Song("b", "u", "s", "Nắng Ấm", lyrics=b),
             Song("m", "u", "s", "Mưa Rơi x Nắng Ấm", lyrics=a[:200] + " " + b[:200]),
             # a version of "Mưa Rơi" whose title names the singer: almost all of it is "Mưa Rơi"
             Song("v", "u", "s", "Mưa Rơi - Ca Sĩ Tèo", lyrics=" ".join(a.split()[:55]) + " ôi ôi"),
             # an original that two medleys borrow from is not a medley itself
             Song("m2", "u", "s", "Mashup Nắng Ấm", lyrics=b[:250])]
    cluster_of = {s.doc_id: s.doc_id for s in songs}
    found = find_compilations(songs, cluster_of)
    assert set(found) == {"m"} and found["m"] == ["a", "b"]


def test_composer_and_singers():
    from visong.corpus import composer_of, credits, singers_of
    s = Song("hac1", "u", "hopamchuan.com", "Bài", artist="Nhạc ngoại", extra={"singers": "Mỹ Tâm, Hà Anh Tuấn"})
    assert composer_of(s) == "" and singers_of(s) == ["Mỹ Tâm", "Hà Anh Tuấn"]
    t = Song("hac2", "u", "hopamchuan.com", "Bài", artist="Phan Mạnh Quỳnh", extra={"singers": "Phan Mạnh Quỳnh"})
    assert credits(t) == "Phan Mạnh Quỳnh"


def test_clean_lyrics_drops_interlude_labels():
    assert clean_lyrics("Gian tấu:\nGiang tấu: x2\nem về\n(Giang tấu)") == "em về"


def test_repair_split_syllables_joins_parts_cut_around_chords():
    from visong.corpus import repair_split_syllables
    songs = [Song(f"s{i}", "u", "s", "t", lyrics=f"giọt sương rơi lặng lẽ bài {i}\nem về") for i in range(25)]
    songs.append(Song("x", "u", "s", "t", lyrics="một chút sươ ng, ngõ vắng lặ ng\nem về"))
    assert repair_split_syllables(songs) == 2
    assert songs[-1].lyrics == "một chút sương, ngõ vắng lặng\nem về"
    assert songs[0].lyrics.startswith("giọt sương rơi lặng lẽ")


def test_clean_lyrics_drops_key_and_chord_lines():
    raw = "Tone chuan Eb. Capo 1\nTông: Am\nAm Em C G\nVòng --- hết cả bài\n__em về\nEm"
    assert clean_lyrics(raw) == "em về\nEm"


def test_romanised_chinese_is_not_vietnamese():
    from visong.corpus import pinyin_ratio, vietnamese_only_ratio
    vi = "Ngày mai em đi, biển nhớ tên em gọi về"
    assert pinyin_ratio(vi) == 0 and vietnamese_only_ratio(vi) > 0.1
    assert pinyin_ratio("wǒ ài nǐ nǐ ài wǒ") > 0.5
    assert vietnamese_only_ratio("shăng gãn ní hǎo zài jiàn") < 0.1
