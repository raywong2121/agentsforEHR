"""Self-learning write side: summarize failure modes from a batch of runs into
strategy drafts (pending human approval).

Flow: predictions + gold -> error grouping -> LLM drafts -> experience_drafts.jsonl
(pending). Approval happens in approve.py; drafts never auto-enter the library.
"""
import json
import sys

import pipeline

DRAFT_PROMPT = (
    "下面是同一概念在临床编码中的错误记录：\n"
    "概念：{concept}\n"
    "错误现象：{error}\n"
    "金标准概念：{gold}\n"
    "预测概念：{pred}\n\n"
    "请把它抽象成一条可迁移的编码策略（不是术语映射），只输出 JSON 对象：\n"
    '{{"name":"策略名(不超过10字)","trigger":"触发条件：什么情况下适用",'
    '"action":"具体动作：该怎么选/怎么排除","examples":["{concept}→{gold}"],"tags":["..."]}}\n'
    "要求：action 必须是可执行的动作，且能推广到同类概念，不要写成某一个词的特例。"
)


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


def collect_errors(pred_path, gold_path):
    gold = json.load(open(gold_path, encoding="utf-8"))["gold"]
    preds = {p["id"]: p for p in json.load(open(pred_path, encoding="utf-8"))["preds"]}
    errors = []
    for g in gold:
        if g["id"] not in preds:      # only cases present in this batch (prevents cross-fold leakage)
            continue
        rows = preds.get(g["id"], {}).get("rows", [])
        used = set()
        for m in g["mentions"]:
            i, r = _match(m["span"], rows, used)
            if r is None:
                errors.append({"concept": m["span"], "error": "整条漏抽",
                               "gold": m.get("concept_name", str(m.get("concept_id"))), "pred": "None"})
                continue
            used.add(i)
            if m.get("concept_id") is None:
                if r.get("status") == "mapped":
                    errors.append({"concept": m["span"], "error": "本应弃权却硬映射",
                                   "gold": "unmappable", "pred": r.get("concept_name", "")})
                continue
            if r.get("status") != "mapped":
                errors.append({"concept": m["span"], "error": "应映射却弃权/候选不足",
                               "gold": m.get("concept_name", ""), "pred": "None"})
            elif r.get("concept_id") != m.get("concept_id") and r.get("concept_id") not in (m.get("alt_ids") or []):
                errors.append({"concept": m["span"], "error": "选错概念",
                               "gold": m.get("concept_name", ""), "pred": r.get("concept_name", "")})
    return errors


def group(errors):
    g = {}
    for e in errors:
        key = e["concept"]
        g.setdefault(key, []).append(e)
    return g


def is_reusable(concept, items):
    """Quality gate: keep only reusable experience that lands on a concrete concept; reject generic "scan the whole text / never leave blank" advice."""
    sample = items[0]
    if sample["error"] in ("整条漏抽", "应映射却弃权/候选不足"):
        return False          # extraction-level failure; the fixes are boilerplate and stay out
    if sample["error"] == "选错概念":
        return True           # wrong concept with a correct target: reusable
    return False


CONFLICT_PROMPT = (
    "以下是系统既有的编码策略（均为经人工确认的原则）：\n{existing}\n\n"
    "候选新策略：\n名称：{name}\n触发：{trigger}\n动作：{action}\n\n"
    "判断该候选策略是否与既有策略冲突或方向相反。特别注意：若既有策略要求"
    "\"在原文支持时选更具体的概念\"，而新策略要求\"回退到上位/泛化概念\"，则属于冲突。\n"
    "只输出 JSON：{{\"conflict\": true 或 false, \"reason\": \"一句话\"}}"
)


def conflicts_with_library(draft):
    """Consistency check against the existing strategy library; conflicts are rejected. Prevents learning the gold bias as strategy."""
    try:
        import strategy
        lib = strategy.load()
        if not lib:
            return False, ""
        existing = "\n".join(
            f"- {s.get('name')}: {s.get('action')}" for s in lib[:12])
        prompt = CONFLICT_PROMPT.format(
            existing=existing,
            name=draft.get("name", ""),
            trigger=draft.get("trigger", ""),
            action=draft.get("action", ""))
        raw = pipeline.llm([{"role": "user", "content": prompt}], max_tokens=80, json_mode=True)
        d = json.loads(raw)
        if isinstance(d, dict) and d.get("data"):
            d = d["data"]
        return bool(d.get("conflict")), str(d.get("reason", ""))[:120]
    except Exception:
        return False, ""


def draft(concept, items):
    sample = items[0]
    prompt = DRAFT_PROMPT.format(concept=concept, error=sample["error"], gold=sample["gold"], pred=sample["pred"])
    try:
        raw = pipeline.llm([{"role": "user", "content": prompt}], max_tokens=220, json_mode=True)
        d = json.loads(raw)
        if isinstance(d, dict) and d.get("data"):
            d = d["data"]
        d["count"] = len(items)
        conflict, why = conflicts_with_library(d)
        if conflict:
            d["status"] = "rejected"
            d["reject_reason"] = "conflicts with existing strategies: " + why
        else:
            d["status"] = "pending"
        return d
    except Exception as e:
        return {"name": concept, "trigger": "", "action": f"(draft generation failed: {str(e)[:60]})",
                "examples": [], "tags": [], "count": len(items), "status": "pending"}


def main(pred_path, gold_path, out_path="experience_drafts.jsonl"):
    errors = collect_errors(pred_path, gold_path)
    groups = group(errors)
    with open(out_path, "w", encoding="utf-8") as f:
        for concept, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            if not is_reusable(concept, items):
                continue
            d = draft(concept, items)
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"{len(errors)} errors in {len(groups)} groups; drafts written to {out_path}")
    for line in open(out_path, encoding="utf-8"):
        d = json.loads(line)
        print(f"  [{d.get('count')}x] {d.get('name') or d.get('concept')}: "
              f"{(d.get('trigger') or d.get('error') or '')[:40]} -> {(d.get('action') or d.get('fix') or '')[:60]}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "predictions100.json",
         sys.argv[2] if len(sys.argv) > 2 else "gold100.json")
