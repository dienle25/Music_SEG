import json, re
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, unquote


def clean(v):
    return ' '.join(str(v or '').replace('\\/', '/').split()).strip()


def meta(soup, *names):
    for n in names:
        t=soup.find('meta', attrs={'property': n}) or soup.find('meta', attrs={'name': n})
        if t and t.get('content'):
            return clean(t['content'])
    return ''


def jsonld(soup):
    for sc in soup.find_all('script', type=lambda x: x and 'ld+json' in x):
        raw=sc.get_text(strip=True)
        try: data=json.loads(raw)
        except Exception:
            continue
        objs=data if isinstance(data,list) else [data]
        for o in objs:
            if not isinstance(o,dict): continue
            typ=o.get('@type',[]); typ=typ if isinstance(typ,list) else [typ]
            if not any(t in ('MusicRecording','AudioObject') for t in typ): continue
            artist=o.get('byArtist') or o.get('artist') or ''
            if isinstance(artist,dict): artist=artist.get('name','')
            if isinstance(artist,list): artist=', '.join(clean(x.get('name','') if isinstance(x,dict) else x) for x in artist)
            album=o.get('inAlbum') or o.get('album') or ''
            if isinstance(album,dict): album=album.get('name','')
            image=o.get('image','')
            if isinstance(image,list): image=image[0] if image else ''
            return {'title':clean(o.get('name')),'artist':clean(artist),'album':clean(album),
                    'thumbnail':clean(image),'duration':clean(o.get('duration'))}
    return {}


def song_url_candidates(html, base_url):
    """Find song URLs even when NCT puts them in data-* attributes or inline JS/JSON."""
    out=[]
    soup=BeautifulSoup(html,'html.parser')
    attrs=('href','data-href','data-url','data-link','data-target','data-song-url','content')
    for tag in soup.find_all(True):
        for attr in attrs:
            val=tag.get(attr)
            if not val: continue
            vals=val if isinstance(val,list) else [val]
            for v in vals:
                s=unquote(str(v)).replace('\\/','/')
                for m in re.findall(r'(?:https?:)?//(?:www\.)?nhaccuatui\.com/(?:bai-hat|song)/[^\"\'<>\s]+|/(?:bai-hat|song)/[^\"\'<>\s]+',s,re.I):
                    u=urljoin(base_url,m).split('#')[0]
                    if u not in out: out.append(u)
    # raw HTML fallback
    for m in re.findall(r'(?:https?:)?//(?:www\.)?nhaccuatui\.com/(?:bai-hat|song)/[^\"\'<>\\s]+|/(?:bai-hat|song)/[^\"\'<>\\s]+', html, re.I):
        u=urljoin(base_url, m).split('#')[0]
        if u not in out: out.append(u)
    return out


def parse(html, url, chart_url=''):
    soup=BeautifulSoup(html,'html.parser')
    title=clean(soup.title.get_text(' ',strip=True) if soup.title else '')
    music=jsonld(soup)
    if not music.get('title'): music['title']=meta(soup,'og:title','twitter:title') or title
    if not music.get('thumbnail'): music['thumbnail']=meta(soup,'og:image','twitter:image')
    if not music.get('artist'): music['artist']=meta(soup,'music:musician','author')
    path=urlparse(url).path.lower()
    is_song='/bai-hat/' in path or '/song/' in path
    # Heuristic fallback for NCT song pages if structured data is absent.
    if is_song and not music.get('artist'):
        h=soup.find(['h1','h2'])
        if h: music['title']=clean(h.get_text(' ',strip=True)) or music.get('title','')
    song=None
    if is_song and music.get('title'):
        song={'title':music.get('title') or title,'artist':music.get('artist',''),
              'album':music.get('album',''),'category':'NhacCuaTui','url':url,
              'thumbnail':music.get('thumbnail',''),'duration':music.get('duration',''),
              'chart_url':chart_url}
    links=song_url_candidates(html,url)
    return title, list(dict.fromkeys(links)), song
