import sqlite3

c = sqlite3.connect("data/crawler.db")

print("=== Pages from chiasenhac.vn ===")
for r in c.execute("SELECT url, status_code, depth FROM pages WHERE domain LIKE '%chiasenhac%'"):
    print(r)

print()
print("=== Links pointing to chiasenhac.vn ===")
for r in c.execute("SELECT source_url, target_url FROM links WHERE target_url LIKE '%chiasenhac%' LIMIT 10"):
    print(r)

print()
print("=== Total links containing chiasenhac ===")
count = c.execute("SELECT COUNT(*) FROM links WHERE target_url LIKE '%chiasenhac%'").fetchone()[0]
print(count)