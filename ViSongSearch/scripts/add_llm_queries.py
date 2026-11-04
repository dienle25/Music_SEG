"""Append hand-checked LLM-style queries (Q4 paraphrase, Q5 description) to queries/llm.jsonl.

Input file: one block per song, `#<item index>` then `Q4: ...` and `Q5: ...` lines.
Every query is checked with visong.queries.check_llm_query; rejected ones are reported
and not written, so they can be rewritten.
"""
import json, os, re, sys
sys.path.insert(0, '.')
from visong.corpus import load_corpus
from visong.queries import check_llm_query, write_unmarked_variants
from visong.text import clean

work, src, generator = sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 'llm'
items = json.load(open(f'{work}/llm_prep/{sys.argv[4] if len(sys.argv) > 4 else "items.json"}'))
songs = {s.doc_id: s for s in load_corpus(f'{work}/corpus.jsonl')}
out_path = f'{work}/queries/llm.jsonl'
existing = {}
if os.path.exists(out_path):
    for l in open(out_path, encoding='utf-8'):
        q = json.loads(l)
        existing[q['qid']] = q
cur, ok, bad = None, 0, []
for line in open(src, encoding='utf-8'):
    line = line.strip()
    if not line:
        continue
    m = re.match(r'^#(\d+)', line)
    if m:
        cur = items[int(m.group(1))]
        continue
    m = re.match(r'^(Q4|Q5):\s*(.+)$', line)
    if not m or cur is None:
        continue
    qtype, text = m.group(1), clean(m.group(2))
    good, why = check_llm_query(qtype, text, songs[cur['doc_id']], cur['frag'] if qtype == 'Q4' else None)
    if not good:
        bad.append((cur['doc_id'], qtype, why, text))
        continue
    qid = f"{cur['doc_id']}-{qtype}-0"
    existing[qid] = {'qid': qid, 'type': qtype, 'text': text, 'doc_id': cur['doc_id'], 'cluster': cur['cluster'],
                     'split': cur['split'], 'meta': {'source_fragment': cur['frag'] if qtype == 'Q4' else None,
                                                     'generator': generator, 'length': len(text.split())}}
    ok += 1
with open(out_path, 'w', encoding='utf-8') as f:
    for q in existing.values():
        f.write(json.dumps(q, ensure_ascii=False) + '\n')
write_unmarked_variants(work)
print(f'written {ok}, total {len(existing)}, rejected {len(bad)}')
for b in bad:
    print('  REJECT', b)
