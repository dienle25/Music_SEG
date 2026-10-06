from bs4 import BeautifulSoup


def parse_html(html):
    soup = BeautifulSoup(html, "html.parser")

    if soup.title:
        title = soup.title.get_text(" ", strip=True)
    else:
        title = ""

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    content = soup.get_text(" ", strip=True)

    links = []
    for tag in soup.find_all("a", href=True):
        links.append(tag["href"])

    return title, content, links
