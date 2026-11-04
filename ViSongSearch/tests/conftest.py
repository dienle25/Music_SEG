import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# The tests build their models locally; without this, loading them waits on the Hugging Face hub.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
