"""统计分析与细分：Wilson 95% 置信区间、分域、分错误类型。"""
import json
import math
import os
import subprocess
import sys


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def fmt(k, n):
    lo, hi = wilson(k, n)
    p = k / n * 100 if n else 0
    return f"{p:.1f}% [{lo*100:.1f}, {hi*100:.1f}]"


def _match(span, rows, used):
    for i, r in enumerate(rows):
        if i not in used and r["span"] == span:
            return i, r
    for i, r in enumerate(rows):
        if i not in used and r["span"] and (span in r["span"] or r["span"] in span):
            return i, r
    return None, None


def concept_domains(ids):
    env = dict(os.environ, PGPASSWORD=os.environ.get("OHDSI_PASSWORD", ""), PGCLIENTENCODING="UTF8")
    ids = [i for i in ids if i]
    if not ids:
        return {}
    sql = ("select c.concept_id, d.domain_id from concept c "
           "join domain d on c.domain_id = d.domain_id where c.concept_id in (%s);" % ",".join(map(str, ids)))
    out = subprocess.run(["psql", "-h", os.environ.get("OHDSI_PGHOST", "127.0.0.1"), "-p", os.environ.get("OHDSI_PGPORT", "5432"), "-U", os.environ.get("OHDSI_PGUSER", "ohdsi_user"), "-d", os.environ.get("OHDSI_PGDB", "ohdsi"),
                          "-tA", "-F", "\t", "-c", sql], env=env, capture_output=True, text=True).stdout
    m = {}
    for line in out.splitlines():
        if "\t" in line:
            cid, dom = line.split("\t", 1)
            m[int(cid)] = dom
    return m


def main(pred_path, gold_path):
    gold = json.load(open(gold_path, encoding="utf-8"))["gold"]
    preds = {p["id"]: p for p in json.load(open(pred_path, encoding="utf-8"))["preds"]}

    total = should = mapped_hit = 0
    silent = 0
    over = 0
    errs = {"wrong_concept": 0, "missed": 0, "over_mapped": 0}
    by_domain = {}
    hit_ids, all_ids = [], []

    for g in gold:
        rows = preds.get(g["id"], {}).get("rows", [])
        used = set()
        for m in g["mentions"]:
            total += 1
            if m.get("concept_id") is None:
                continue
            should += 1
            hit_ids.append(m["concept_id"])
            i, r = _match(m["span"], rows, used)
            if r is None:
                silent += 1
                errs["missed"] += 1
                dom = "UNK"
                by_domain.setdefault(dom, [0, 0])
                by_domain[dom][1] += 1
                continue
            used.add(i)
            ok = (r.get("status") == "mapped"
                  and (r.get("concept_id") == m["concept_id"] or r.get("concept_id") in (m.get("alt_ids") or [])))
            if ok:
                mapped_hit += 1
        # 过映射
        for i, r in enumerate(rows):
            if i not in used and r.get("status") == "mapped":
                over += 1
                errs["over_mapped"] += 1

    doms = concept_domains(hit_ids)
    # 分域统计
    by_domain = {}
    for g in gold:
        rows = preds.get(g["id"], {}).get("rows", [])
        used = set()
        for m in g["mentions"]:
            if m.get("concept_id") is None:
                continue
            dom = doms.get(m["concept_id"], "UNK")
            by_domain.setdefault(dom, [0, 0])
            by_domain[dom][1] += 1
            i, r = _match(m["span"], rows, used)
            if r is None:
                continue
            used.add(i)
            if (r.get("status") == "mapped"
                    and (r.get("concept_id") == m["concept_id"] or r.get("concept_id") in (m.get("alt_ids") or []))):
                by_domain[dom][0] += 1

    print(f"文件: {pred_path}")
    print(f"应映射 {should} / 总 mention {total}")
    print(f"严格命中      : {fmt(mapped_hit, should)}")
    print(f"覆盖率        : {fmt(should - silent, should)}   (静默丢失 {silent})")
    print(f"过映射        : {over}")
    print("\n分域严格命中：")
    for dom, (k, n) in sorted(by_domain.items(), key=lambda x: -x[1][1]):
        if n >= 3:
            print(f"  {dom:<14} {fmt(k, n)}  (n={n})")
    return mapped_hit, should


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "predictions_real100_v2.json",
         sys.argv[2] if len(sys.argv) > 2 else "gold_real100.json")
