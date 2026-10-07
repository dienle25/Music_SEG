from crawler import Crawler
if __name__ == "__main__":
    c=Crawler()
    try:c.run()
    finally:c.close()
