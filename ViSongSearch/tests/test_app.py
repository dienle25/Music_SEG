import importlib
import json

import pytest

from visong.corpus import Song, passages_of, write_jsonl


@pytest.fixture()
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    songs = [Song("a", "https://x/a", "test", "Mưa Rơi", "Ca Sĩ A", lyrics="mưa rơi trên phố vắng\nem về đâu khi chiều buông\n" * 6),
             Song("b", "https://x/b", "test", "Đường Xa", "Ca Sĩ B", lyrics="đường xa vạn dặm\nanh vẫn chờ em\n" * 6)]
    for s in songs:
        s.extra = {"cluster": s.doc_id, "split": "test"}
    write_jsonl(str(tmp_path / "corpus.jsonl"), songs)
    write_jsonl(str(tmp_path / "passages.jsonl"), [p for s in songs for p in passages_of(s)])
    json.dump({"a": "a", "b": "b"}, open(tmp_path / "clusters.json", "w"))
    monkeypatch.setenv("VISONG_WORK", str(tmp_path))
    monkeypatch.setenv("VISONG_LOG", str(tmp_path / "log.sqlite"))
    import app.main
    importlib.reload(app.main)
    return TestClient(app.main.app)


def test_search_song_page_and_logs(client):
    r = client.get("/api/search", params={"q": "duong xa van dam"})
    assert r.status_code == 200 and r.json()["results"][0]["doc_id"] == "b"
    page = client.get("/search", params={"q": "mưa rơi trên phố"})
    assert page.status_code == 200 and "Mưa Rơi" in page.text
    song = client.get("/song/a")
    assert "MusicRecording" in song.text and "https://x/a" in song.text
    assert client.get("/song/zzz").status_code == 404
    assert "<loc>" in client.get("/sitemap.xml").text
    assert "Disallow: /search" in client.get("/robots.txt").text
    stats = client.get("/stats")
    assert stats.status_code == 200
