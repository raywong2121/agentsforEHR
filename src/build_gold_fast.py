"""Fast gold construction: retrieve once per deduplicated (en, domain), then assemble."""
import json
import sys
from retrieve import retrieve

src, dst = sys.argv[1], sys.argv[2]
cases = json.load(open(src, encoding="utf-8"))["cases"]

need = set()
for c in cases:
    for m in c["mentions"]:
        if not m.get("unmappable") and m.get("concept_id") is None and m.get("en"):
            need.add((m["en"], m.get("domain")))
cache = {}
for en, dom in sorted(need, key=lambda x: (x[0], str(x[1]))):
    c = retrieve(en, dom)
    cache[(en, dom)] = c[0] if c else None
    print("  ", (en, dom), "->", (cache[(en, dom)] or {}).get("concept_id"), flush=True)

gold = []
for c in cases:
    ms = []
    for m in c["mentions"]:
        site_txt, site_en = m.get("site"), m.get("site_en")
        site_cid = None
        if site_en:
            sc = retrieve(site_en, "Spec Anatomic Site")
            site_cid = sc[0]["concept_id"] if sc else None
        if m.get("unmappable"):
            ms.append({"span": m["span"], "concept_id": None, "assertion": m.get("assertion", ""),
                       "subject": m.get("subject", ""), "value": m.get("value"),
                       "specimen": m.get("specimen"), "site": site_txt, "site_concept_id": site_cid})
            continue
        if m.get("concept_id") is not None:
            ms.append({"span": m["span"], "concept_id": m["concept_id"], "assertion": m.get("assertion", ""),
                       "subject": m.get("subject", ""), "value": m.get("value"),
                       "specimen": m.get("specimen"), "site": site_txt, "site_concept_id": site_cid})
            continue
        top = cache.get((m["en"], m.get("domain")))
        ms.append({"span": m["span"], "concept_id": top["concept_id"] if top else None,
                   "concept_name": top["concept_name"] if top else "",
                   "assertion": m.get("assertion", ""), "subject": m.get("subject", ""),
                   "value": m.get("value"), "specimen": m.get("specimen"),
                   "site": site_txt, "site_concept_id": site_cid})
    gold.append({"id": c["id"], "text": c["text"], "mentions": ms})
json.dump({"gold": gold}, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("built", dst, "cases", len(gold), "mentions", sum(len(g['mentions']) for g in gold))
