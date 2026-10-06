import sqlite3
c = sqlite3.connect("data/crawler.db")
rows = c.execute("SELECT url, content FROM pages WHERE status_code=200 LIMIT 3").fetchall()
for url, content in rows:
    print(url)
    print((content or "")[:300])
    print("---")