# -*- coding: utf-8 -*-
"""按病例聚类的配对 bootstrap：比较两个系统在同一批病例上的严格命中率差值。"""
import json
import random
import statistics
import sys

random.seed(20261109)


def _match(span, rows, used):
    for i, r in enumerate(rows):
        if i not in used and r["span"] == span:
            return i, r
    for i, r in enumerate(rows):
        if i not in used and r["span"] and (span in r["span"] or r["span"] in span):
            return i, r
    return None, None


def per_case(pred_path, gold):
    preds = {p["id"]: p for p in json.load(open(pred_path, encoding="utf-8"))["preds"]}
    out = {}
    for g in gold:
        rows = preds.get(g["id"], {}).get("rows", [])
        used = set()
        n = hit = 0
        for m in g["mentions"]:
            if m.get("concept_id") is None:
                continue
            n += 1
            i, r = _match(m["span"], rows, used)
            if r is None:
                continue
            used.add(i)
            if (r.get("status") == "mapped"
                    and (r.get("concept_id") == m["concept_id"] or r.get("concept_id") in (m.get("alt_ids") or []))):
                hit += 1
        out[g["id"]] = (hit, n)
    return out


def main(a_path, b_path, gold_path):
    gold = json.load(open(gold_path, encoding="utf-8"))["gold"]
    A = per_case(a_path, gold)
    B = per_case(b_path, gold)

    ids = [g["id"] for g in gold if g["id"] in A and g["id"] in B]
    n_fact = sum(A[i][1] for i in ids)
    ha = sum(A[i][0] for i in ids)
    hb = sum(B[i][0] for i in ids)
    print(f"病例 {len(ids)}，事实 {n_fact}")
    print(f"A: {ha}/{n_fact} = {ha/n_fact*100:.1f}%   ({a_path})")
    print(f"B: {hb}/{n_fact} = {hb/n_fact*100:.1f}%   ({b_path})")
    print(f"差值 A-B: {(ha-hb)/n_fact*100:+.1f} 个百分点")

    # 病例级 bootstrap
    diffs = []
    for _ in range(5000):
        samp = [random.choice(ids) for _ in ids]
        ta = sum(A[i][1] for i in samp)
        if ta == 0:
            continue
        da = sum(A[i][0] for i in samp) / ta
        db = sum(B[i][0] for i in samp) / ta
        diffs.append(da - db)
    diffs.sort()
    lo = diffs[int(0.025 * len(diffs))]
    hi = diffs[int(0.975 * len(diffs))]
    print(f"病例级 bootstrap 95% CI of (A-B): [{lo*100:+.1f}, {hi*100:+.1f}] 个百分点")
    print(f"（差值 > 0 的 bootstrap 比例：{sum(1 for d in diffs if d > 0)/len(diffs)*100:.1f}%）")


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["predictions_real100_v2.json", "predictions_zeroshot.json", "gold_real100.json"]))
