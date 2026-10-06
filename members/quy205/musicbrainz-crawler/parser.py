from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from config import BLOCKED_EXTENSIONS, BLOCKED_SCHEMES


def normalize_url(base_url, href):
    """Convert a relative link to an absolute, normalized HTTP(S) URL."""
    if not href:
        return None

    absolute = urljoin(base_url, href.strip())
    parsed = urlparse(absolute)

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        return None

    # Remove fragments because /page#section and /page are the same
    # crawl target for this assignment.
    normalized = urlunparse((
        scheme,
        parsed.netloc.lower(),
        parsed.path or "/",
        parsed.params,
        parsed.query,
        "",
    ))

    return normalized


def is_allowed_url(url, allowed_domains):
    """Apply protocol, domain and file-type filtering rules."""
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        return False

    hostname = (parsed.hostname or "").lower()
    allowed = any(
        hostname == domain or hostname.endswith("." + domain)
        for domain in allowed_domains
    )
    if not allowed:
        return False

    path_lower = parsed.path.lower()
    if any(path_lower.endswith(ext) for ext in BLOCKED_EXTENSIONS):
        return False

    if parsed.scheme.lower() in BLOCKED_SCHEMES:
        return False

    return True


def extract_page_info(html):
    """Extract title and visible text from an HTML document."""
    soup = BeautifulSoup(html, "html.parser")

    if soup.title:
        title = soup.title.get_text(" ", strip=True)
    else:
        title = ""

    # Remove elements that are not visible page text.
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    content = soup.get_text(separator=" ", strip=True)
    return title, content


def extract_links(html, current_url):
    """Extract and normalize all hyperlink targets."""
    soup = BeautifulSoup(html, "html.parser")
    links = []

    for tag in soup.find_all("a", href=True):
        url = normalize_url(current_url, tag["href"])
        if url:
            links.append(url)

    return links
