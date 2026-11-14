# -*- coding: utf-8 -*-
"""Full six-dimension evaluation: overall + per-domain + Wilson 95% CIs + per-dim detail; writes JSON for the paper tables."""
import json
import math
import re
import sys


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def fmt(k, n):
    lo, hi = wilson(k, n)
    return f"{k/n*100:.1f}% [{lo*100:.1f}–{hi*100:.1f}]"


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


SUBJ_MAP = {"患者": "self", "患儿": "self", "本人": "self",
            "患者父亲": "family", "患者母亲": "family", "患者家属": "family", "家属": "family",
            "胎儿": "fetus"}


def main(pred_path, gold_path):
    gold = json.load(open(gold_path, encoding="utf-8"))["gold"]
    preds = {p["id"]: p for p in json.load(open(pred_path, encoding="utf-8"))["preds"]}

    DIMS = ["concept", "assertion", "value", "specimen", "subject", "site", "all"]
    overall = {d: [0, 0] for d in DIMS}
    by_domain = {}
    missed = []

    for g in gold:
        rows = preds.get(g["id"], {}).get("rows", [])
        used = set()
        for m in g["mentions"]:
            i, r = _match(m["span"], rows, used)
            if r is None:
                missed.append((g["id"], m["span"]))
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
            checks["value"] = (val_norm(r.get("value")) == val_norm(m.get("value")))
            checks["specimen"] = ((r.get("specimen") or None) == (m.get("specimen") or None))
            checks["subject"] = SUBJ_MAP.get(r.get("subject", ""), r.get("subject", "")) == \
                                SUBJ_MAP.get(m.get("subject", "患者"), m.get("subject", "患者"))
            checks["site"] = ((r.get("site_concept_id") or None) == (m.get("site_concept_id") or None))
            all_ok = True
            for d in DIMS[:6]:
                overall[d][1] += 1
                if checks.get(d):
                    overall[d][0] += 1
                else:
                    all_ok = False
            overall["all"][1] += 1
            if all_ok:
                overall["all"][0] += 1

            dom = (r.get("domain") or "UNK") if checks["concept"] else "UNK"
            bd = by_domain.setdefault(dom, {d: [0, 0] for d in DIMS})
            for d in DIMS[:6]:
                bd[d][1] += 1
                if checks.get(d):
                    bd[d][0] += 1
            bd["all"][1] += 1
            if all_ok:
                bd["all"][0] += 1

    out = {"overall": {d: {"k": overall[d][0], "n": overall[d][1],
                           "str": fmt(overall[d][0], overall[d][1])} for d in DIMS},
           "by_domain": {dom: {d: fmt(v[d][0], v[d][1]) for d in DIMS}
                         for dom, v in sorted(by_domain.items(), key=lambda x: -x[1]["all"][1])},
           "missed": missed}
    json.dump(out, open("eval6d_full_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("=== overall (Wilson 95% CI) ===")
    for d in DIMS:
        print(f"  {d:<9} {out['overall'][d]['str']}  ({out['overall'][d]['k']}/{out['overall'][d]['n']})")
    print("\n=== per-domain (all-correct) ===")
    for dom, v in out["by_domain"].items():
        print(f"  {dom:<14} {v['all']}")
    print(f"\nunmatched {len(missed)}:", missed[:10])
    return out


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "predictions100_6d.json",
         sys.argv[2] if len(sys.argv) > 2 else "gold_real100.json")
