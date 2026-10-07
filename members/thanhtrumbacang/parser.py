import json,re
from bs4 import BeautifulSoup
from urllib.parse import urljoin,urlparse,urldefrag,unquote
SONG_MARKERS=("/bai-hat/","/song/")
def clean(v):
    v=str(v or "").replace("\xa0"," "); v=re.sub(r"[ \t\r\f\v]+"," ",v); v=re.sub(r"\n[ \t]+","\n",v); return v.strip()
def normalize_url(url,base=None):
    if not url:return ""
    if base:url=urljoin(base,url)
    url=unquote(url).replace("\\/","/"); url,_=urldefrag(url); p=urlparse(url)
    if p.scheme not in ("http","https") or p.netloc.lower() not in {"nhaccuatui.com","www.nhaccuatui.com"}:return ""
    return "https://www.nhaccuatui.com"+p.path
def is_song_url(url): return bool(url) and any(x in urlparse(url).path.lower() for x in SONG_MARKERS)
def meta(soup,*names):
    for n in names:
        x=soup.find("meta",attrs={"property":n}) or soup.find("meta",attrs={"name":n})
        if x and x.get("content"):return clean(x["content"])
    return ""
def jsonld_song(soup):
    for sc in soup.find_all("script",type=lambda x:x and "ld+json" in x):
        try:data=json.loads(sc.get_text(strip=True))
        except:continue
        for o in (data if isinstance(data,list) else [data]):
            if not isinstance(o,dict):continue
            typ=o.get("@type",[]); typ=typ if isinstance(typ,list) else [typ]
            if not any(str(t).lower() in {"musicrecording","audioobject"} for t in typ):continue
            a=o.get("byArtist") or o.get("artist") or ""
            if isinstance(a,dict):a=a.get("name","")
            elif isinstance(a,list):a=", ".join(x.get("name","") if isinstance(x,dict) else str(x) for x in a)
            return {"title":clean(o.get("name","")),"artist":clean(a),"genre":clean(o.get("genre",""))}
    return {}
def extract_song_links(html,base):
    soup=BeautifulSoup(html,"html.parser"); out=set()
    for tag in soup.find_all(True):
        for attr in ("href","data-href","data-url","data-link","data-target","data-song-url"):
            val=tag.get(attr)
            if val:
                for v in (val if isinstance(val,list) else [val]):
                    u=normalize_url(v,base)
                    if is_song_url(u):out.add(u)
    pat=re.compile(r'(?:(?:https?:)?//(?:www\.)?nhaccuatui\.com)?/(?:bai-hat|song)/[^"\'<>\s\\]+',re.I)
    for m in pat.findall(html):
        u=normalize_url(m,base)
        if is_song_url(u):out.add(u)
    return out
def extract_artist(soup,j):
    if j.get("artist"):return j["artist"]
    for sel in ('[class*="artist"] a','[class*="singer"] a','[class*="artist"]','[class*="singer"]'):
        n=soup.select_one(sel)
        if n and clean(n.get_text(" ",strip=True)):return clean(n.get_text(" ",strip=True))
    return meta(soup,"music:musician","author")
def extract_genre(soup,j):
    if j.get("genre"):return j["genre"]
    for sel in ('[class*="genre"]','[class*="category"]','[class*="type-song"]'):
        n=soup.select_one(sel)
        if n and clean(n.get_text(" ",strip=True)):return clean(n.get_text(" ",strip=True))
    return meta(soup,"music:genre","genre")
def extract_lyrics(soup):
    cand=[]
    for sel in (".pd_lyric",".lyric",".lyrics","[class*='lyric-content']","[class*='lyrics-content']","[class*='lyric']","[id*='lyric']","[id*='lyrics']"):
        for n in soup.select(sel):
            for b in n.select("script,style,noscript,button,form"):b.decompose()
            t=re.sub(r"\n{3,}","\n\n",n.get_text("\n",strip=True)).strip()
            if len(t)>=30:cand.append(t)
    if cand:return max(cand,key=len)
    return ""
def parse_page(html,url):
    soup=BeautifulSoup(html,"html.parser"); j=jsonld_song(soup); title=j.get("title","") or meta(soup,"og:title","twitter:title") or clean(soup.title.get_text(" ",strip=True) if soup.title else "")
    title=re.sub(r"\s*[-|]\s*(NhacCuaTui|Nhaccuatui).*$","",title,flags=re.I).strip()
    song={"url":normalize_url(url),"title":clean(title),"artist":clean(extract_artist(soup,j)),"genre":clean(extract_genre(soup,j)),"lyrics":extract_lyrics(soup)} if is_song_url(url) and title else None
    return title,extract_song_links(html,url),song
