import time, requests
from collections import deque
from urllib.parse import urlparse, urldefrag, urljoin
from urllib.robotparser import RobotFileParser
from datetime import datetime, timezone
from config import *
from database import Database
from parser import parse

class Crawler:
    def __init__(self):
        self.db=Database(DB_PATH); self.q=deque((u,0) for u in SEED_URLS); self.seen=set(); self.discovered=set(SEED_URLS)
        self.session=requests.Session(); self.session.headers.update({'User-Agent':USER_AGENT}); self.robots={}; self.saved=0
    def norm(self,u,base=None):
        if base: u=urljoin(base,u)
        u,_=urldefrag(u); p=urlparse(u)
        if p.scheme not in ('http','https'): return None
        h=p.netloc.lower(); h=h[4:] if h.startswith('www.') else h
        if not any(h==d or h.endswith('.'+d) for d in ALLOWED_DOMAINS): return None
        if any(p.path.lower().endswith(x) for x in IGNORED_EXTENSIONS): return None
        return f'https://www.nhaccuatui.com{p.path or "/"}' + (('?'+p.query) if p.query else '')
    def allowed(self,u):
        if not RESPECT_ROBOTS: return True
        p=urlparse(u); host=p.netloc.lower()
        if host not in self.robots:
            rp=RobotFileParser(f'{p.scheme}://{p.netloc}/robots.txt')
            try: rp.read(); self.robots[host]=rp
            except Exception: self.robots[host]=None
        if self.robots[host] is None:
            print('  robots.txt unavailable -> continue'); return True
        return self.robots[host].can_fetch(USER_AGENT,u)
    def add(self,source,depth,links):
        for link in links:
            u=self.norm(link,source)
            if not u or u in self.seen or u in self.discovered: continue
            if depth+1>MAX_DEPTH: continue
            self.discovered.add(u); self.q.append((u,depth+1)); self.db.save_link(source,u)
    def run(self):
        while self.q and len(self.seen)<MAX_PAGES:
            u,d=self.q.popleft()
            if u in self.seen: continue
            self.seen.add(u)
            if not self.allowed(u): print('SKIP robots:',u); continue
            try:
                r=self.session.get(u,timeout=REQUEST_TIMEOUT); print(f'[{len(self.seen):03d}] {r.status_code} {u}')
            except requests.RequestException as e: print('REQUEST ERROR:',e); continue
            title=''; links=[]; song=None
            if r.ok and 'text/html' in r.headers.get('content-type','').lower():
                title,links,song=parse(r.text,u,SEED_URLS[0]); print(f'   Song links found: {len(links)}'); self.add(u,d,links)
                if song and self.db.save_song(song): self.saved+=1; print('   SONG:',song['title'],'|',song['artist'])
            self.db.save_page(u,title,r.status_code,d); time.sleep(CRAWL_DELAY)
        print('\nDONE'); print('Pages:',len(self.seen)); print('Discovered:',len(self.discovered)); print('Songs saved:',self.saved); print('Database:',DB_PATH)
    def close(self): self.db.close()

if __name__=='__main__':
    c=Crawler()
    try: c.run()
    finally: c.close()
