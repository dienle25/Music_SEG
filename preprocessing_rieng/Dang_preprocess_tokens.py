# ===== Dang_preprocess_tokens: tokenize / stopword / stem / lemma / bỏ dấu =====
import re, unicodedata
import nltk
from nltk.stem import PorterStemmer, WordNetLemmatizer

def _ensure(pkg, path):
    try:
        nltk.data.find(path)
    except LookupError:
        try: nltk.download(pkg, quiet=True)
        except Exception as e: print("Không tải được", pkg, e)

for _p, _path in [("punkt","tokenizers/punkt"), ("punkt_tab","tokenizers/punkt_tab"),
                  ("stopwords","corpora/stopwords"), ("wordnet","corpora/wordnet"),
                  ("omw-1.4","corpora/omw-1.4")]:
    _ensure(_p, _path)

def tokenize(text):
    """word_tokenize; nếu thiếu punkt thì dùng NLTKWordTokenizer (cùng bộ tách từ, không tách câu)."""
    try:
        from nltk.tokenize import word_tokenize
        return word_tokenize(text)
    except LookupError:
        from nltk.tokenize import NLTKWordTokenizer
        return NLTKWordTokenizer().tokenize(text)

# ---- Từ phủ định: KHÔNG bao giờ bỏ ----
NEGATIONS = {"không","khong","chẳng","chang","chưa","chua","đừng","dung","chớ","chả",
             "not","no","never","nor","n't","nt","cannot","without","none","nothing","nobody","nowhere"}

def _is_neg(w):
    return w in NEGATIONS or w.endswith("n't") or w.endswith("nt") and w[:-2] in {
        "do","does","did","is","are","was","were","have","has","had","would","could","should","can","won","wo","ai","need","must","might","ca","sha"}

def build_en_stopwords():
    try:
        from nltk.corpus import stopwords
        sw = set(stopwords.words("english"))
    except LookupError:
        sw = set()
    # chuẩn hoá giống clean_text (bỏ dấu nháy: don't -> dont) rồi loại hết dạng phủ định
    sw |= {w.replace("'", "") for w in sw}
    return {w for w in sw if not _is_neg(w)}

# Hư từ tiếng Việt (tự soạn, dựa trên các token xuất hiện nhiều nhất của kho — xem Step 4).
# Đại từ nhân xưng (anh, em, tôi, mình...) KHÔNG đưa vào mặc định vì mang nghĩa trong lời nhạc.
VI_STOPWORDS = {
    "và","là","của","thì","những","đã","sẽ","này","các","một","có","cho","với","trong","được",
    "để","đang","khi","mà","cũng","như","nhưng","rằng","vì","nên","ở","ra","vào","lại","nữa",
    "đó","kia","ấy","nào","rồi","còn","hay","hoặc","cả","mọi","từng","bởi","tại","theo","từ",
    "đến","lên","xuống","thế","vẫn","cứ","chỉ","đều","rất","quá","thật","sao","nhé","nha","ơi",
    "à","ừ","ạ","hả","thôi","luôn","mỗi","bao","nhiêu","gì","đâu","ai","vậy","thế","đây",
}
VI_STOPWORDS -= NEGATIONS

def build_stopwords():
    return (build_en_stopwords() | VI_STOPWORDS) - NEGATIONS

STOPWORDS = build_stopwords()
_stemmer, _lemmatizer = PorterStemmer(), WordNetLemmatizer()

def strip_diacritics(s):
    """lạc trôi -> lac troi ; đ -> d"""
    s = unicodedata.normalize("NFD", s.replace("đ","d").replace("Đ","D"))
    return unicodedata.normalize("NFC", "".join(c for c in s if unicodedata.category(c) != "Mn"))

def _wn_pos(tag):
    from nltk.corpus import wordnet
    return {"J": wordnet.ADJ, "V": wordnet.VERB, "N": wordnet.NOUN, "R": wordnet.ADV}.get(tag[:1], wordnet.NOUN)

def lemmatize_tokens(tokens):
    """Như PDF: pos_tag rồi WordNetLemmatizer. Nếu thiếu tagger/wordnet thì lùi về thử n/v, lấy dạng ngắn hơn."""
    try:
        return [_lemmatizer.lemmatize(t, _wn_pos(tag)) for t, tag in nltk.pos_tag(tokens)]
    except LookupError:
        out = []
        for t in tokens:
            try: out.append(min(_lemmatizer.lemmatize(t, "n"), _lemmatizer.lemmatize(t, "v"), key=len))
            except LookupError: out.append(t)
        return out

def preprocess_tokens(text, strategy="baseline"):
    """text: chuỗi ĐÃ qua clean_text. Dùng CHUNG cho bài hát và query.
    baseline      : tokenize + giữ token chữ/số (isalnum), như tokenize_for_search trong PDF
    no_stopwords  : baseline + bỏ stop word (EN + VI, giữ phủ định)
    stemmed       : no_stopwords + PorterStemmer          (như PDF Step 7)
    lemmatized    : no_stopwords + WordNetLemmatizer(POS) (như PDF Step 7)
    no_diacritics : baseline + bỏ dấu (lạc trôi -> lac troi)
    Có thể ghép bằng '+', vd 'no_stopwords+no_diacritics'."""
    parts = set(strategy.split("+")) - {"baseline"}
    bad = parts - {"no_stopwords","stemmed","lemmatized","no_diacritics"}
    if bad: raise ValueError(f"strategy không hợp lệ: {bad}")
    if parts & {"stemmed","lemmatized"}: parts.add("no_stopwords")
    text = text.replace("'", "").replace("\u2019", "")                     # don't -> dont
    text = re.sub(r"[^\w\s]|_", " ", text)                                   # dấu câu, gạch nối -> khoảng trắng (m-tp -> m tp); giữ chữ có dấu
    toks = [t for t in tokenize(text) if t.isalnum()]
    if "no_stopwords" in parts:  toks = [t for t in toks if t not in STOPWORDS]
    if "lemmatized" in parts:    toks = lemmatize_tokens(toks)
    if "stemmed" in parts:       toks = [_stemmer.stem(t) for t in toks]
    if "no_diacritics" in parts: toks = [strip_diacritics(t) for t in toks]
    return toks
