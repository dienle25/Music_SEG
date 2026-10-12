import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import ranking  # noqa: E402


def make_doc(url, title, body):
    return {
        "url": url,
        "title": title,
        "title_tokens": ranking.tokenize(title),
        "body_tokens": ranking.tokenize(body),
    }


def sample_docs():
    return [
        make_doc("a", "Nhạc trẻ hot", "bài hát nhạc trẻ menu"),
        make_doc("b", "Bảng xếp hạng", "bảng xếp hạng tuần menu"),
        make_doc("c", "Nhạc Hoa", "nhạc hoa menu"),
    ]


class TestPreprocessing(unittest.TestCase):
    def test_strip_accents(self):
        self.assertEqual(ranking.strip_accents("Nhạc Trẻ Đẹp"), "Nhac Tre Dep")

    def test_tokenize_stopword_and_bigram(self):
        tokens = ranking.tokenize("Nhạc trẻ và tình yêu")
        self.assertNotIn("va", tokens)
        self.assertIn("nhac", tokens)
        self.assertIn("nhac_tre", tokens)
        self.assertIn("tinh_yeu", tokens)

    def test_tokenize_none(self):
        self.assertEqual(ranking.tokenize(None), [])


class TestRanker(unittest.TestCase):
    def setUp(self):
        self.ranker = ranking.Ranker(sample_docs())

    def test_best_match_first(self):
        results, _ = self.ranker.score("nhạc trẻ")
        self.assertEqual(results[0]["doc"]["url"], "a")

    def test_non_matching_doc_excluded(self):
        results, _ = self.ranker.score("nhạc trẻ")
        self.assertNotIn("b", [r["doc"]["url"] for r in results])

    def test_unknown_query(self):
        results, unknown = self.ranker.score("xyzqwerty")
        self.assertEqual(results, [])
        self.assertTrue(unknown)

    def test_scores_sorted_and_normalized(self):
        results, _ = self.ranker.score("nhạc")
        scores = [r["score"] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(0 <= s <= 1.0 + 1e-9 for s in scores))

    def test_alpha_extremes(self):
        only_bm25, _ = self.ranker.score("nhạc trẻ", alpha=1)
        only_tfidf, _ = self.ranker.score("nhạc trẻ", alpha=0)
        for r in only_bm25:
            self.assertAlmostEqual(r["score"], r["bm25"])
        for r in only_tfidf:
            self.assertAlmostEqual(r["score"], r["tfidf"])

    def test_boilerplate_removed(self):
        ranker = ranking.Ranker(sample_docs(), max_df_ratio=0.5)
        self.assertIn("menu", ranker.boilerplate)


class TestLoadDocuments(unittest.TestCase):
    def test_pages_only_status_200_with_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.db")
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE pages (url TEXT, title TEXT, content TEXT, status_code INTEGER)"
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, ?)",
                [
                    ("u1", "Nhạc Hot", "bài hát hot", 200),
                    ("u2", "Lỗi", "", 500),
                    ("u3", "Rỗng", None, 200),
                ],
            )
            conn.commit()
            conn.close()
            docs = ranking.load_documents(path, "pages")
            self.assertEqual([d["url"] for d in docs], ["u1"])

    def test_songs_artist_suffix_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.db")
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE songs (url TEXT, title TEXT, artist TEXT, lyrics TEXT)"
            )
            conn.execute(
                "INSERT INTO songs VALUES ('s1', 'Bài A', 'Ca Sĩ X | NHAC.VN', 'lời bài hát')"
            )
            conn.commit()
            conn.close()
            docs = ranking.load_documents(path, "songs")
            self.assertEqual(docs[0]["title"], "Bài A - Ca Sĩ X")


if __name__ == "__main__":
    unittest.main()
