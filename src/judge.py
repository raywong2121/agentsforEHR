"""语义等价判定：concept_id 不同不等于错。用模型判断金标准概念与预测概念是否临床可互换。

只对"两边都映射但 id 不同"的 mention 调模型，成本可控。
输出：exact / same / related / different 四类，并给出语义准确率。
"""
import json
import sys

import pipeline

PROMPT = (
    "临床片段「{span}」。\n"
    "概念A（金标准）：{gold}（{gold_id}）\n"
    "概念B（预测）：{pred}（{pred_id}）\n"
    "在临床概念编码中，这两个概念是否等价或可互换？只输出一个词：same（同义/可互换）、"
    "related（相关但粒度不同）、different（不同）。"
)


def judge(span, gold, gold_id, pred, pred_id):
    raw = pipeline.llm([{"role": "user", "content": PROMPT.format(
        span=span, gold=gold, gold_id=gold_id, pred=pred, pred_id=pred_id)}], max_tokens=10)
    out = raw.strip().lower().replace("```", "")
    for k in ("same", "related", "different"):
        if k in out:
            return k
    return "unknown"


def _match(span, rows, used):
    for i, r in enumerate(rows):
        if i in used:
            continue
        if r["span"] == span:
            return i, r
    for i, r in enumerate(rows):
        if i in used:
            continue
        if span in r["span"] or r["span"] in span:
            return i, r
    return None, None


def main(pred_path, gold_path):
    gold = json.load(open(gold_path, encoding="utf-8"))["gold"]
    preds = {p["id"]: p for p in json.load(open(pred_path, encoding="utf-8"))["preds"]}
    total = should = exact = same = related = different = miss = 0
    details = []
    for g in gold:
        rows = preds.get(g["id"], {}).get("rows", [])
        used = set()
        for m in g["mentions"]:
            total += 1
            if m.get("concept_id") is None:
                continue
            should += 1
            i, r = _match(m["span"], rows, used)
            if r is None or r.get("status") != "mapped":
                miss += 1
                continue
            used.add(i)
            pid = r.get("concept_id")
            if pid == m["concept_id"] or pid in (m.get("alt_ids") or []):
                exact += 1
                continue
            verdict = judge(m["span"], m.get("concept_name", ""), m["concept_id"],
                            r.get("concept_name", ""), pid)
            details.append((g["id"], m["span"], m.get("concept_name"), r.get("concept_name"), verdict))
            if verdict == "same":
                same += 1
            elif verdict == "related":
                related += 1
            else:
                different += 1
    semantic = (exact + same) / should if should else 0
    loose = (exact + same + related) / should if should else 0
    print(f"应映射 {should} | 精确命中 {exact} | 语义等价 {same} | 相关 {related} | 不同 {different} | 漏 {miss}")
    print(f"语义准确率(精确+same) : {semantic*100:.1f}%")
    print(f"宽松准确率(+related)   : {loose*100:.1f}%")
    print("\n--- 非精确的逐条判定 ---")
    for gid, span, gn, pn, v in details:
        print(f"  [{gid}] {span}: 金={gn} 预={pn} -> {v}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "predictions_hard.json",
         sys.argv[2] if len(sys.argv) > 2 else "hard_gold.json")
