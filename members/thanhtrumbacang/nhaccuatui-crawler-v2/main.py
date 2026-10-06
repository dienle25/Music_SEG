from crawler import Crawler
from config import *
print('='*58); print(' NHA CUA TUI MUSIC CRAWLER + SQLITE'); print('='*58)
print('Seed:',SEED_URLS[0]); print('Max pages:',MAX_PAGES); print('Database:',DB_PATH); print()
c=Crawler()
try: c.run()
finally: c.close()
