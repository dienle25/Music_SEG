import time,urllib.robotparser,requests
from collections import deque
from urllib.parse import urlparse
from config import *
from database import Database
from parser import normalize_url,parse_page
class Crawler:
    def __init__(self):
        self.db=Database(DB_PATH); self.session=requests.Session(); self.session.headers.update({"User-Agent":USER_AGENT,"Accept-Language":"vi-VN,vi;q=0.9,en;q=0.8"}); self.queue=deque(); self.seen=set(); self.discovered=set(); self.robots={}; self.pages=0; self.failed=0; self.saved=self.db.count_songs()
        for s in SEED_URLS:
            u=normalize_url(s)
            if u:self.queue.append((u,0));self.discovered.add(u)
    def robots_allowed(self,url):
        if not RESPECT_ROBOTS:return True
        p=urlparse(url); root=f"{p.scheme}://{p.netloc}"
        if root not in self.robots:
            rp=urllib.robotparser.RobotFileParser(root+"/robots.txt")
            try:rp.read();self.robots[root]=rp
            except:self.robots[root]=None
        return self.robots[root] is not None and self.robots[root].can_fetch(USER_AGENT,url)
    def fetch(self,url):
        err=None
        for i in range(1,MAX_RETRIES+1):
            try:
                r=self.session.get(url,timeout=REQUEST_TIMEOUT,allow_redirects=True);r.raise_for_status(); u=normalize_url(r.url)
                if not u:raise ValueError("redirect URL invalid")
                return r,u
            except Exception as e:
                err=e
                if i<MAX_RETRIES:time.sleep(1.5*i)
        raise err
    def enqueue(self,source,links,depth):
        if depth+1>MAX_DEPTH:return
        for link in links:
            u=normalize_url(link,source)
            if u and u not in self.seen and u not in self.discovered:
                self.discovered.add(u);self.queue.append((u,depth+1));self.db.save_link(source,u)
    def run(self):
        print("Target:",TARGET_SONGS,"Existing:",self.saved,"Album: REMOVED")
        while self.queue and self.pages<MAX_PAGES and self.saved<TARGET_SONGS:
            url,depth=self.queue.popleft()
            if url in self.seen:continue
            self.seen.add(url);self.pages+=1
            if not self.robots_allowed(url):print("[ROBOTS]",url);continue
            try:
                r,final=self.fetch(url); print(f"[{self.pages:04d}] {r.status_code} {final}")
                if "text/html" not in r.headers.get("content-type","").lower():continue
                title,links,song=parse_page(r.text,final); self.db.save_page(final,urlparse(final).netloc,title,r.text,depth,r.status_code); self.enqueue(final,links,depth)
                if song and self.db.save_song(song):
                    self.saved+=1;print(f"   + SONG {self.saved}/{TARGET_SONGS}: {song['title']} | lyric={'CÓ' if song['lyrics'] else 'KHÔNG TÌM THẤY'}")
            except Exception as e:self.failed+=1;print("   ERROR:",type(e).__name__,e)
            time.sleep(CRAWL_DELAY)
        self.db.commit(); print("\nPages:",self.pages,"Discovered:",len(self.discovered),"Songs:",self.saved,"Failed:",self.failed); print("SUCCESS - đủ 300 bài" if self.saved>=TARGET_SONGS else f"CHƯA ĐỦ: {self.saved}/300")
    def close(self):self.db.close()
if __name__=="__main__":
    c=Crawler()
    try:c.run()
    finally:c.close()
