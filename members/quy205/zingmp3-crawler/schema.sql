CREATE TABLE songs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    artist TEXT,
    category TEXT,
    url TEXT UNIQUE NOT NULL,
    thumbnail TEXT NOT NULL,
    duration TEXT,
    lyric_url TEXT,
    lyric_status TEXT,
    lyric_excerpt TEXT,
    lyric_chars INTEGER DEFAULT 0,
    lyric_hash TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
