# -*- coding: utf-8 -*-
"""Two-arm comparison: same batch of cases, frozen strategies vs online-updated strategies.

设计要点：
1. 两臂共享同一份抽取结果（CACHE_EXTRACT=1），只让策略库不同，压住运行间波动；
2. 每折先跑冻结臂（恢复 10 条种子），再跑在线臂（当前已增长的库）；
3. 在线臂跑完，从其失败中挖矿（含一致性闸门）并经"批准"入库。
"""
import json
import os
import shutil

import approve
import mine
import pipeline
import strategy

HERE = os.path.dirname(os.path.abspath(__file__))
STRAT = os.path.join(HERE, "experience_strategies.jsonl")
FROZEN = os.path.join(HERE, "strategies_frozen10.jsonl")
RESULT = os.path.join(HERE, "two_arm_result.json")


def run_fold(fold):
    preds = []
    for i, c in enumerate(fold, 1):
        try:
            r = pipeline.run_case(c["text"])
            r["id"] = c["id"]
            preds.append(r)
        except Exception as e:
            preds.append({"id": c["id"], "text": c["text"], "rows": [], "error": str(e)[:150]})
        if i % 5 == 0:
            print(f"      {i}/{len(fold)}", flush=True)
    return preds


def score(preds, gold_map):
    total = correct = 0
    for p in preds:
        g = gold_map.get(p["id"])
        if not g:
            continue
        rows = p.get("rows", [])
        used = set()
        for m in g["mentions"]:
            if m.get("concept_id") is None:
                continue
            total += 1
            idx = None
            for i, r in enumerate(rows):
                if i not in used and r["span"] == m["span"]:
                    idx = i
                    break
            if idx is None:
                for i, r in enumerate(rows):
                    if i not in used and r["span"] and (m["span"] in r["span"] or r["span"] in m["span"]):
                        idx = i
                        break
            if idx is None:
                continue
            used.add(idx)
            r = rows[idx]
            if r["status"] == "mapped" and (r["concept_id"] == m["concept_id"]
                                            or r["concept_id"] in (m.get("alt_ids") or [])):
                correct += 1
    return total, correct


def main():
    if not os.path.exists(FROZEN):
        shutil.copy(STRAT, FROZEN)
    frozen = [json.loads(l) for l in open(FROZEN, encoding="utf-8") if l.strip()]
    seeds = [json.loads(l) for l in open(STRAT, encoding="utf-8") if l.strip()]
    grown = list(seeds)          # online arm's library (maintained separately; never overwritten by the frozen arm)
    open(STRAT, "w", encoding="utf-8").write("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in seeds))

    cases = json.load(open(os.path.join(HERE, "cases_real100.json"), encoding="utf-8"))["cases"]
    gold = json.load(open(os.path.join(HERE, "gold_real100.json"), encoding="utf-8"))["gold"]
    gold_map = {g["id"]: g for g in gold}

    K = 5
    size = len(cases) // K
    folds = [cases[i * size:(i + 1) * size] for i in range(K)]
    if len(cases) > size * K:
        folds[-1].extend(cases[size * K:])

    out = []
    for k, fold in enumerate(folds, 1):
        print(f"\n=== fold {k} ({len(fold)} cases) ===", flush=True)

        # frozen arm
        open(STRAT, "w", encoding="utf-8").write(
            "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in frozen))
        print("  frozen arm...", flush=True)
        predsF = run_fold(fold)
        tF, cF = score(predsF, gold_map)
        json.dump({"preds": predsF}, open(os.path.join(HERE, f"arm_frozen_fold{k}.json"), "w",
                                          encoding="utf-8"), ensure_ascii=False, indent=1)

        # online arm (current grown library)
        open(STRAT, "w", encoding="utf-8").write(
            "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in grown))
        cur = list(grown)
        print(f"  online arm (library of {len(cur)})...", flush=True)
        predsO = run_fold(fold)
        tO, cO = score(predsO, gold_map)
        json.dump({"preds": predsO}, open(os.path.join(HERE, f"arm_online_fold{k}.json"), "w",
                                          encoding="utf-8"), ensure_ascii=False, indent=1)

        row = {"fold": k, "n": tF,
               "frozen": round(cF / tF * 100, 1) if tF else 0,
               "online": round(cO / tO * 100, 1) if tO else 0,
               "diff": round((cO / tO - cF / tF) * 100, 1) if tF and tO else 0,
               "lib_before": len(cur), "lib_after": len(cur)}
        out.append(row)
        print(f"  >>> frozen {row['frozen']}% | online {row['online']}% | diff {row['diff']:+} pt", flush=True)

        # online arm failures -> mine -> approve
        try:
            pred_path = os.path.join(HERE, f"arm_online_fold{k}.json")
            mine.main(pred_path, os.path.join(HERE, "gold_real100.json"), os.path.join(HERE, "experience_drafts.jsonl"))
            drafts = approve.load_drafts()
            n = 0
            for d in drafts:
                if d.get("status") == "pending":
                    approve.append_experience(d)
                    d["status"] = "approved"
                    n += 1
            approve.save_drafts(drafts)
            grown = [json.loads(l) for l in open(STRAT, encoding="utf-8") if l.strip()]
            row["lib_after"] = len(grown)
            print(f"  {n} new strategies -> library of {row['lib_after']}", flush=True)
        except Exception as e:
            print("  mining/approval failed:", str(e)[:150], flush=True)

        json.dump(out, open(RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("\n=== two-arm results ===", flush=True)
    for r in out:
        print(f"  fold {r['fold']} | frozen {r['frozen']}% | online {r['online']}% | diff {r['diff']:+} pt "
              f"| library {r['lib_before']}->{r['lib_after']}", flush=True)
    tf = sum(r["n"] for r in out) or 1
    print(f"  weighted mean diff: {sum(r['diff'] * r['n'] for r in out) / tf:+.1f} pt", flush=True)
    json.dump(out, open(RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
