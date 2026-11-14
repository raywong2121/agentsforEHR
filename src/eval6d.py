# -*- coding: utf-8 -*-
"""Six-dimension evaluation: concept / assertion / value / specimen / subject / site, plus the all-correct rate."""
import json
import re
import sys


def _match(span, rows, used):
    for i, r in enumerate(rows):
        if i not in used and r["span"] == span:
            return i, r
    for i, r in enumerate(rows):
        if i not in used and r["span"] and (span in r["span"] or r["span"] in span):
            return i, r
    return None, None


def val_norm(v):
    if v is None:
        return None
    s = str(v).strip().lower().replace(" ", "")
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)", s)
    return m.group(1) if m else s


def same_value(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return val_norm(a) == val_norm(b)


SUBJ_MAP = {"患者": "self", "患儿": "self", "本人": "self",
            "患者父亲": "family", "患者母亲": "family", "患者家属": "family", "家属": "family",
            "胎儿": "fetus"}


def same_subject(a, b):
    return SUBJ_MAP.get(a, a) == SUBJ_MAP.get(b, b)


def main(pred_path, gold_path):
    gold = json.load(open(gold_path, encoding="utf-8"))["gold"]
    preds = {p["id"]: p for p in json.load(open(pred_path, encoding="utf-8"))["preds"]}

    dims = {"concept": [0, 0], "assertion": [0, 0], "value": [0, 0],
            "specimen": [0, 0], "subject": [0, 0], "site": [0, 0], "all": [0, 0]}
    n_gold_mentions = 0
    detail = []

    for g in gold:
        rows = preds.get(g["id"], {}).get("rows", [])
        used = set()
        for m in g["mentions"]:
            n_gold_mentions += 1
            i, r = _match(m["span"], rows, used)
            if r is None:
                continue
            used.add(i)
            checks = {}
            if m.get("concept_id") is not None:
                checks["concept"] = (r.get("status") == "mapped"
                                     and (r.get("concept_id") == m["concept_id"]
                                          or r.get("concept_id") in (m.get("alt_ids") or [])))
            else:
                checks["concept"] = r.get("status") != "mapped"
            checks["assertion"] = (r.get("assertion", "") == m.get("assertion", ""))
            checks["value"] = same_value(r.get("value"), m.get("value"))
            checks["specimen"] = ((r.get("specimen") or None) == (m.get("specimen") or None))
            checks["subject"] = same_subject(r.get("subject", ""), m.get("subject", "患者"))
            checks["site"] = ((r.get("site_concept_id") or None) == (m.get("site_concept_id") or None))
            all_ok = True
            for k in ("concept", "assertion", "value", "specimen", "subject", "site"):
                dims[k][1] += 1
                if checks.get(k):
                    dims[k][0] += 1
                else:
                    all_ok = False
            dims["all"][1] += 1
            if all_ok:
                dims["all"][0] += 1
            else:
                detail.append((g["id"], m["span"], {k: v for k, v in checks.items() if not v}))

    print(f"gold mentions {n_gold_mentions} | matched rows {dims['concept'][1]}")
    for k in ("concept", "assertion", "value", "specimen", "subject", "site", "all"):
        c, n = dims[k]
        print(f"  {k:<9} {c}/{n} = {c/n*100:.1f}%" if n else f"  {k}: n/a")
    print("\nfacts failing any dimension (first 20):")
    for cid, span, fails in detail[:20]:
        print(f"  [{cid}] {span}: failing dims {list(fails)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "preds_fold1_logged.json",
         sys.argv[2] if len(sys.argv) > 2 else "gold_fold1_corrected.json")
