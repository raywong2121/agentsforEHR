# -*- coding: utf-8 -*-
"""验证"决策路径"策略：把两个失败模式写成确定性检查，在带日志的 fold-1 上离线回放。

检查 1（过弃权修复）：弃权时，若顶部候选覆盖原文核心英文词且域一致 → 改为接受。
检查 2（限定词粒度修正）：已选概念若存在"核心词覆盖更多、多余限定词更少"的候选 → 改选它。
统计：修对（错→对）、改坏（对→错）、以及疑似金标准错误的清单。
"""
import json
import re

FOLD1_IDS = {f"R{i:03d}" for i in range(1, 21)}

STOP = {"of", "the", "in", "with", "and", "or", "a", "an", "to", "for", "at", "on",
        "no", "not", "due", "left", "right", "bilateral", "unspecified", "n", "os",
        "de", "la", "ii", "iii", "acute", "chronic"}


def tokens(s):
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def core(row):
    ens0 = (row.get("ens") or [""])[0]
    return tokens(ens0) - STOP


def fix_abstain(row):
    if row.get("status") == "mapped":
        return None
    cands = row.get("candidates") or []
    if not cands:
        return None
    c = core(row)
    if not c:
        return None
    top = cands[0]
    if c <= tokens(top["name"]) and top.get("domain") == row.get("domain"):
        return top["id"]
    return None


def revise_selection(row):
    if row.get("status") != "mapped":
        return None
    cands = row.get("candidates") or []
    if len(cands) < 2:
        return None
    c = core(row)
    if not c:
        return None

    def score(x):
        t = tokens(x["name"])
        return (len(c & t), -len(t - c), -int(x.get("src", 9)))

    best = max(cands, key=score)
    if best["id"] != row.get("concept_id"):
        return best["id"]
    return None


def match_row(span, rows, used):
    for i, r in enumerate(rows):
        if i not in used and r["span"] == span:
            return i, r
    for i, r in enumerate(rows):
        if i not in used and r["span"] and (span in r["span"] or r["span"] in span):
            return i, r
    return None, None


def main():
    gold = json.load(open("gold_real100.json", encoding="utf-8"))["gold"]
    preds = {p["id"]: p for p in json.load(open("preds_fold1_logged.json", encoding="utf-8"))["preds"]}

    fixed = broken = unchanged_err = 0
    fixed_cases, broken_cases, suspect = [], [], []

    for g in gold:
        if g["id"] not in FOLD1_IDS:
            continue
        rows = preds.get(g["id"], {}).get("rows", [])
        used = set()
        for m in g["mentions"]:
            if m.get("concept_id") is None:
                continue
            i, r = match_row(m["span"], rows, used)
            if i is not None:
                used.add(i)
            if r is None:
                continue
            orig_ok = (r.get("status") == "mapped" and r.get("concept_id") == m["concept_id"])
            new = fix_abstain(r) or revise_selection(r)
            if orig_ok:
                if new is not None:
                    broken += 1
                    broken_cases.append((g["id"], m["span"], r.get("concept_name"), new))
            else:
                if new is not None and new == m["concept_id"]:
                    fixed += 1
                    fixed_cases.append((g["id"], m["span"], m.get("concept_name")))
                else:
                    unchanged_err += 1
                    cands = r.get("candidates") or []
                    suspect.append({
                        "id": g["id"], "span": m["span"],
                        "gold": m.get("concept_name", str(m.get("concept_id"))),
                        "pred": r.get("concept_name") or ("弃权" if r else "未抽取"),
                        "top3": [f"{c['name']}({c['id']})" for c in cands[:3]],
                    })

    print(f"错误 {fixed + unchanged_err} 个 | 检查修对 {fixed} | 改坏正确项 {broken} | 未修 {unchanged_err}")
    print()
    print("修对的：")
    for cid, span, name in fixed_cases:
        print(f"  [{cid}] {span} -> {name}")
    if broken_cases:
        print("\n改坏的（需防）：")
        for cid, span, old, new in broken_cases:
            print(f"  [{cid}] {span}: {old} -> {new}")
    print(f"\n未修/疑似金标准错误 {len(suspect)} 条 -> 裁定清单 suspect_cases.json")
    json.dump(suspect, open("suspect_cases.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
