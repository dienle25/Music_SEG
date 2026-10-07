import sqlite3
from pathlib import Path

class Database:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(path)
        self.create_tables()
    def create_tables(self):
        self.conn.execute("CREATE TABLE IF NOT EXISTS links (id INTEGER PRIMARY KEY AUTOINCREMENT, source_url TEXT, target_url TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS pages (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE, domain TEXT, title TEXT, content TEXT, depth INTEGER, status_code INTEGER, crawled_at TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS songs (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE, title TEXT, artist TEXT, genre TEXT, lyrics TEXT, crawled_at TEXT)")
        self.conn.commit()
    def count_songs(self): return self.conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    def song_exists(self,url): return self.conn.execute("SELECT 1 FROM songs WHERE url=? LIMIT 1",(url,)).fetchone() is not None
    def save_link(self,s,t): self.conn.execute("INSERT INTO links(source_url,target_url) VALUES (?,?)",(s,t))
    def save_page(self,url,domain,title,content,depth,status):
        self.conn.execute("INSERT OR REPLACE INTO pages(url,domain,title,content,depth,status_code,crawled_at) VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)",(url,domain,title,content,depth,status))
    def save_song(self,s):
        if not s.get("url") or not s.get("title"): return False
        cur=self.conn.execute("INSERT OR IGNORE INTO songs(url,title,artist,genre,lyrics,crawled_at) VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)",(s["url"],s.get("title",""),s.get("artist",""),s.get("genre",""),s.get("lyrics","")))
        self.conn.commit(); return cur.rowcount==1
    def commit(self): self.conn.commit()
    def close(self): self.conn.close()
