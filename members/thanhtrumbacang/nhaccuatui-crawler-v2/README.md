# NhacCuaTui Music Crawler + SQLite

## Run on Windows

```powershell
cd music_web_crawler
python -m pip install -r requirements.txt
python main.py
```

The crawler starts from a NhacCuaTui chart, finds `/bai-hat/` and `/song/` links, fetches individual song pages, extracts title/artist/album/thumbnail/duration and saves them to `data/music.db`.

Open `data/music.db` in DB Browser for SQLite and choose **Browse Data -> songs**.

The included database contains a small seed snapshot from a public NhacCuaTui chart so the database is not empty before the first run. These seed rows are marked with the chart URL and have no invented song URL; the live crawler fills real song URLs when it fetches the chart.

If the site blocks requests or changes HTML, the crawler may save fewer songs. Do not disable robots restrictions to bypass a site's rules.

## Fix v2
This version extracts NCT song links from href, data-* attributes, and inline HTML/JSON because the chart page may not expose ordinary anchor hrefs.
