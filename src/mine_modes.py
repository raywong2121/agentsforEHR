# -*- coding: utf-8 -*-
"""失败模式挖矿（v2）：从"错误发生在哪条推理路径"中提炼策略，而非从"错误→正确答案"中提炼知识。

流程：
  1. 用候选集判定失败模式（候选缺失 / 子串陷阱 / 层级混淆 / 弃权 / 漏抽）
  2. 每种模式聚合若干真实案例
  3. 让模型针对"该模式的推理路径"提出一个可执行检查，而不是一条概念规则
  4. 输出策略草案（name / trigger / action / examples / mode）
"""
import json
import sys

import pipeline

MODE_DESC = {
    "candidate_missing": "金标准概念没有出现在当时的候选集中——检索阶段没有召回正确概念。",
    "substring_trap": "金标准概念在候选集中，但系统选了另一个与其名称高度相似（互为子串/近名）的概念。",
    "selection_other": "金标准概念在候选集中，但系统选了另一个语义不同的概念（非子串关系）。",
    "abstained": "系统抽到了该片段，但最终选择弃权，而金标准认为应当编码。",
    "missed": "该片段完全没有被抽取出来。",
}

MODE_PROMPT = """你在分析一个临床概念编码智能体的**失败模式**，目标是提炼一条可迁移的**推理检查**，而不是一条"某个词该映射到什么"的知识。

失败模式：{mode_desc}

真实案例（同属该模式）：
{examples}

请只输出 JSON：
{{
  "name": "策略名（不超过10字）",
  "trigger": "触发条件：候选集或原文中出现什么结构特征时，这条检查应当被激活（要描述结构特征，不要点名具体疾病）",
  "action": "检查动作：在接受该概念之前，具体要比较/核对什么，以及核对不通过时怎么做",
  "examples": ["简短示例，格式：原文片段 → 应排除什么 → 应选什么"]
}}

要求：
1. trigger 必须能在**没见过的概念**上被判断出来（例如"候选名互为子串但中心词不同""候选集中同时存在 Measurement 与 Condition 域"）；
2. 不要写"某病应选某概念"这类知识；
3. action 必须是一个可执行的检查步骤。
"""


def classify(row, gold_cid):
    if not row:
        return "missed"
    if row.get("status") != "mapped":
        return "abstained"
    cid = row.get("concept_id")
    if cid == gold_cid:
        return None
    cands = row.get("candidates") or []
    ids = [c["id"] for c in cands]
    if gold_cid not in ids:
        return "candidate_missing"
    gold_name = next((c["name"].lower() for c in cands if c["id"] == gold_cid), "")
    pred_name = (row.get("concept_name") or "").lower()
    if gold_name and pred_name and (gold_name in pred_name or pred_name in gold_name):
        return "substring_trap"
    return "selection_other"


def match_row(span, rows, used):
    for i, r in enumerate(rows):
        if i not in used and r["span"] == span:
            return i, r
    for i, r in enumerate(rows):
        if i not in used and r["span"] and (span in r["span"] or r["span"] in span):
            return i, r
    return None, None


def collect(pred_path, gold_path):
    gold = json.load(open(gold_path, encoding="utf-8"))["gold"]
    preds = {p["id"]: p for p in json.load(open(pred_path, encoding="utf-8"))["preds"]}
    modes = {}
    for g in gold:
        if g["id"] not in preds:
            continue
        rows = preds[g["id"]].get("rows", [])
        used = set()
        for m in g["mentions"]:
            if m.get("concept_id") is None:
                continue
            i, r = match_row(m["span"], rows, used)
            if i is not None:
                used.add(i)
            mode = classify(r, m["concept_id"])
            if not mode:
                continue
            cands = (r or {}).get("candidates") or []
            modes.setdefault(mode, []).append({
                "span": m["span"],
                "gold": m.get("concept_name") or str(m.get("concept_id")),
                "pred": (r or {}).get("concept_name") or ("未映射" if r else "未抽取"),
                "pred_domain": (r or {}).get("domain"),
                "ens": (r or {}).get("ens"),
                "cands": [f"{c['name']}({c['id']},{c['domain']})" for c in cands[:6]],
            })
    return modes


def make_strategy(mode, items):
    examples = "\n".join(
        f"- 片段「{it['span']}」：金标准={it['gold']}；系统选择={it['pred']}；"
        f"英文候选={it['ens']}；候选集={'; '.join(it['cands'][:5])}"
        for it in items[:4])
    prompt = MODE_PROMPT.format(mode_desc=MODE_DESC[mode], examples=examples)
    try:
        raw = pipeline.llm([{"role": "user", "content": prompt}], max_tokens=520, json_mode=True)
        d = json.loads(raw)
        if isinstance(d, dict) and d.get("data"):
            d = d["data"]
        d["mode"] = mode
        d["count"] = len(items)
        d["status"] = "pending"
        return d
    except Exception as e:
        return {"name": mode, "trigger": MODE_DESC[mode], "action": f"（生成失败：{str(e)[:50]}）",
                "examples": [], "mode": mode, "count": len(items), "status": "pending"}


def main(pred_path, gold_path, out_path="experience_drafts.jsonl"):
    modes = collect(pred_path, gold_path)
    total = sum(len(v) for v in modes.values())
    print(f"错误 {total} 个，失败模式 {len(modes)} 类")
    with open(out_path, "w", encoding="utf-8") as f:
        for mode, items in sorted(modes.items(), key=lambda kv: -len(kv[1])):
            print(f"  [{len(items)}x] {mode}: {MODE_DESC[mode]}")
            d = make_strategy(mode, items)
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print("drafts ->", out_path)


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["predictions_real100_v2.json", "gold_real100.json"]))
