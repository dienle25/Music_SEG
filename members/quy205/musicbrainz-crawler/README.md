# Focused Web Crawler - Music

## 1. Project overview

This project implements the **Focused Web Crawler** required in the SEG301
"Crawls and Feeds" practice assignment.

The crawler uses:

- Python 3
- Requests
- BeautifulSoup
- SQLite
- Python standard libraries

The implementation covers the main tasks in the assignment:

1. Seed URLs
2. URL Frontier
3. Breadth-First Search (BFS)
4. HTTP requests with Requests
5. HTML parsing with BeautifulSoup
6. URL filtering
7. Maximum crawl depth
8. Duplicate URL prevention
9. SQLite storage
10. Crawling statistics
11. robots.txt checking

---

## 2. Important topic note

The supplied assignment table gives these example topics:

- News & Information
- Technology
- Movies & Entertainment
- Education

**Music is not listed in that table.**

This project uses Music because the group's selected topic is Music.
Please confirm with the instructor that a Music topic is acceptable before
submitting. If the instructor requires one of the listed topics, replace the
topic and domains in `config.py` with two approved domains from the same row.

---

## 3. Selected topic and domains

Topic:

**Music**

Domains:

- MusicBrainz
- Free Music Archive

Seed URLs:

```text
https://musicbrainz.org/
https://freemusicarchive.org/
```

The first site was tested successfully with Requests and BeautifulSoup in
the student's `test_zing.py` output after changing the test URL to
MusicBrainz. The test returned HTTP 200 and 115 `<a>` tags, demonstrating
that server-rendered HTML and hyperlinks can be extracted.

Before the final submission, run:

```bash
python test_sites.py
```

and record the actual results from your own machine.

---

## 4. Crawling configuration

The configuration is stored in `config.py`.

```text
Maximum pages : 100
Maximum depth : 3
Request timeout: 10 seconds
Crawl delay   : 2 seconds
```

The crawler uses a 2-second delay because the checked MusicBrainz robots.txt
specifies a crawl delay of 2 seconds.

A conservative delay is used globally for the two domains.

---

## 5. BFS crawling strategy

The crawler uses a FIFO URL Frontier implemented with Python `deque`.

Example:

```text
Depth 0:
    Seed A
    Seed B

Depth 1:
    A1
    A2
    B1
    B2

Depth 2:
    A1-1
    A1-2
    A2-1
    ...
```

The crawler processes all URLs at one depth before moving to the next depth.

Each frontier item stores:

```python
(url, depth)
```

This allows the crawler to enforce `MAX_DEPTH`.

---

## 6. URL filtering

The crawler accepts only HTTP and HTTPS URLs.

A URL is rejected when:

- Its scheme is not HTTP/HTTPS.
- Its hostname is outside `ALLOWED_DOMAINS`.
- It points to a non-web resource such as an image, CSS, JavaScript,
  archive, audio, video, or office/PDF file.
- It is a `mailto:`, `tel:`, `javascript:`, `data:`, or `file:` URL.
- It has already been visited.
- It is already waiting in the frontier.
- robots.txt does not allow the crawler to fetch it.
- The discovered depth would exceed `MAX_DEPTH`.

Relative URLs are converted into absolute URLs with `urljoin()`.

URL fragments such as:

```text
https://example.com/page#section
```

are removed during normalization so that they do not create unnecessary
duplicate crawl targets.

---

## 7. robots.txt

The crawler checks `robots.txt` before fetching a URL.

Python's:

```python
urllib.robotparser.RobotFileParser
```

is used to interpret the site's crawling rules.

If robots.txt cannot be obtained, this implementation fails closed and does
not crawl that URL. This is a conservative choice for a student crawler.

The crawler also re-checks the final URL after HTTP redirects.

---

## 8. Page extraction

For HTML pages, BeautifulSoup extracts:

- Page title
- Visible text content
- Hyperlinks

The assignment specifically states that text preprocessing is not required
at this stage.

Therefore this project does NOT perform:

- Tokenization
- Stopword removal
- Stemming
- Lemmatization
- TF-IDF

The raw extracted text is stored in SQLite.

---

## 9. SQLite database

The database is:

```text
data/crawler.db
```

### Table: pages

```sql
CREATE TABLE pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE,
    domain TEXT,
    title TEXT,
    content TEXT,
    depth INTEGER,
    status_code INTEGER,
    crawled_at TEXT
);
```

It stores the information required by the assignment.

### Table: links

```sql
CREATE TABLE links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT,
    target_url TEXT
);
```

This records the relationship between the page containing a link and the
target URL.

---

## 10. Crawling statistics

The crawler calculates and displays:

- Pages crawled
- Unique URLs discovered
- Skipped URLs
- Failed requests
- Maximum depth
- Number of pages at each depth
- HTTP status code counts
- URLs remaining in the frontier

The values are calculated during the crawl rather than manually entered.

---

## 11. Project structure

```text
web-crawler-music-assignment/
│
├── main.py
├── crawler.py
├── url_frontier.py
├── parser.py
├── database.py
├── config.py
├── test_sites.py
├── requirements.txt
├── README.md
├── .gitignore
│
└── data/
    └── crawler.db       # created after running the crawler
```

---

## 12. Installation

Create a virtual environment if desired:

```bash
python -m venv venv
```

Activate it on Windows:

```bash
venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## 13. Test the seed websites

Run:

```bash
python test_sites.py
```

This checks:

- HTTP status
- Page title
- Extracted page text
- Number of hyperlinks
- First 20 hyperlinks

The test should be performed before the full crawl because website
availability and structure can change.

---

## 14. Run the crawler

Run:

```bash
python main.py
```

The program first displays the configuration and then performs BFS crawling.

Example output format:

```text
===============================================================
                 FOCUSED WEB CRAWLER
===============================================================
Topic           : Music
Seed URLs       : 2
  1. https://musicbrainz.org/
  2. https://freemusicarchive.org/
Allowed Domains :
  - musicbrainz.org
  - freemusicarchive.org
Maximum Depth   : 3
Maximum Pages   : 100
Request Timeout : 10 seconds
Crawl Delay     : 2.0 second(s)
===============================================================

[Crawl #001]
Depth : 0
URL   : ...
Status: 200
Time  : ...
Links : ...

...
```

The exact statistics will depend on the websites' current content and
crawling policies, so they must be generated by running the program.

---

## 15. Stopping conditions

The crawler stops when one of the following occurs:

1. `MAX_PAGES` is reached.
2. The URL Frontier becomes empty.
3. No more valid URLs can be discovered within the maximum depth.

---

## 16. Ethical and technical considerations

This project is intentionally limited to:

- HTTP/HTTPS pages
- Approved domains
- Maximum depth 3
- Maximum 100 pages
- A delay between requests
- robots.txt restrictions
- No authentication
- No bypassing of access controls
- No downloading of audio/video files

If a website changes its robots.txt, terms, or technical behavior, the
crawler should be stopped and the configuration reviewed before continuing.

---

## 17. Mapping to assignment tasks

| Assignment task | Implementation |
|---|---|
| Task 1: Seed URLs | `config.py` |
| Task 2: URL Frontier | `url_frontier.py` |
| Task 3: Crawl pages | `crawler.py` |
| Task 4: Extract page information | `parser.py` |
| Task 5: Extract/filter hyperlinks | `parser.py`, `crawler.py` |
| Task 6: Crawl depth | `crawler.py` |
| Task 7: Avoid duplicates | `visited` + frontier set |
| Task 8: SQLite | `database.py` |
| Task 9: Complete crawler | `main.py` + all modules |
| Crawling statistics | `crawler.py`, `main.py` |
| README | This file |

---

## 18. Final results section

After running the crawler, copy the actual final summary from the terminal
into the report/presentation.

Example format:

```text
Topic                  : Music
Seed URLs              : 2
Pages Crawled          : [actual result]
Unique URLs Discovered : [actual result]
Skipped URLs           : [actual result]
Failed Requests        : [actual result]
Maximum Depth          : 3
Depth 0                : [actual result]
Depth 1                : [actual result]
Depth 2                : [actual result]
Depth 3                : [actual result]
HTTP 200               : [actual result]
HTTP 404               : [actual result]
HTTP 403               : [actual result]
Frontier Remaining     : [actual result]
```

Do not invent these numbers. They should come directly from the execution
of `main.py`.
