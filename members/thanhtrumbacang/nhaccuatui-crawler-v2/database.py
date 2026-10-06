import sqlite3
from pathlib import Path

class Database:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.create_tables()

    def create_tables(self):
        self.conn.execute('''CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE, title TEXT, status_code INTEGER, depth INTEGER,
            crawled_at TEXT
        )''')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_url TEXT, target_url TEXT,
            UNIQUE(source_url,target_url)
        )''')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            artist TEXT,
            album TEXT,
            category TEXT,
            url TEXT UNIQUE,
            thumbnail TEXT,
            duration TEXT,
            chart_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        self.conn.commit()

    def save_page(self, url, title, status, depth):
        self.conn.execute('''INSERT INTO pages(url,title,status_code,depth,crawled_at)
            VALUES(?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(url) DO UPDATE SET title=excluded.title,status_code=excluded.status_code''',
            (url,title,status,depth))
        self.conn.commit()

    def save_link(self, source, target):
        self.conn.execute('INSERT OR IGNORE INTO links(source_url,target_url) VALUES(?,?)',(source,target))
        self.conn.commit()

    def save_song(self, s):
        if not s.get('title') or not s.get('url'): return False
        self.conn.execute('''INSERT INTO songs(title,artist,album,category,url,thumbnail,duration,chart_url)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET title=excluded.title,artist=excluded.artist,
            album=excluded.album,thumbnail=excluded.thumbnail,duration=excluded.duration,
            chart_url=excluded.chart_url,updated_at=CURRENT_TIMESTAMP''',
            (s.get('title'),s.get('artist',''),s.get('album',''),s.get('category','NhacCuaTui'),
             s['url'],s.get('thumbnail',''),s.get('duration',''),s.get('chart_url','')))
        self.conn.commit(); return True

    def close(self): self.conn.close()
