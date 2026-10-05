# =========================================================
# check_db.py  –  xem nhanh dữ liệu đã crawl:  python check_db.py
# =========================================================

import sqlite3

from database import DB_PATH

conn = sqlite3.connect(DB_PATH)

print("===== PAGES PER DOMAIN =====")
for domain, count in conn.execute(
        "SELECT domain, COUNT(*) FROM pages GROUP BY domain ORDER BY COUNT(*) DESC"):
    print(f"{domain:<22} {count}")

print("\n===== PAGES =====")
rows = conn.execute("""
SELECT id, domain, depth, status_code, title, LENGTH(content)
FROM pages
ORDER BY id
""").fetchall()

for page_id, domain, depth, status, title, length in rows:
    print(f"{page_id:>3} | {domain:<20} | d={depth} | {status} | {length:>6} chars | {title[:60]}")

print("\n===== LINKS =====")
total = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
print("Total links:", total)

for row in conn.execute("SELECT id, source_url, target_url FROM links LIMIT 20"):
    print(row)

conn.close()
