"""基线实验：零样本大模型直接编码（无检索、无自检、无策略记忆）。

用于回答：智能体相对"LLM 直接编码"的增益有多大。
"""
import json
import sys

import pipeline

PROMPT = (
    "请把下面这段中文病历中的临床概念映射为 OMOP/OHDSI 标准概念（concept_id）。\n"
    "要求：\n"
    "1. 只输出 JSON 数组，每个元素为 {{\"span\":\"原文片段\",\"concept_id\":数字,\"concept_name\":\"标准概念英文名\",\"domain\":\"域\"}}；\n"
    "2. span 必须是原文中出现的中文片段；\n"
    "3. 无法确定标准概念时不要输出该项；\n"
    "4. 只输出 JSON，不要解释。\n\n"
    "病历：{text}"
)


def run_one(case):
    raw = pipeline.llm([{"role": "user", "content": PROMPT.format(text=case["text"])}],
                       max_tokens=900, json_mode=True)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("data", data.get("concepts", data.get("content", data.get("items", []))))
        items = data if isinstance(data, list) else []
    except Exception:
        items = []
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        cid = it.get("concept_id")
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            cid = None
        rows.append({
            "span": str(it.get("span", "")), "zh": str(it.get("span", "")),
            "concept_id": cid, "concept_name": str(it.get("concept_name", "")),
            "domain": str(it.get("domain", "")), "assertion": it.get("assertion", ""),
            "subject": "", "site": None, "site_concept_id": None, "value": it.get("value"),
            "status": "mapped" if cid else "unmapped", "reason": "" if cid else "模型未给出概念",
        })
    return {"id": case["id"], "text": case["text"], "rows": rows}


def main(src="cases_real100.json", dst="predictions_zeroshot.json"):
    cases = json.load(open(src, encoding="utf-8"))["cases"]
    preds = []
    for i, c in enumerate(cases, 1):
        try:
            preds.append(run_one(c))
        except Exception as e:
            preds.append({"id": c["id"], "text": c["text"], "rows": [], "error": str(e)[:150]})
        if i % 10 == 0:
            print(f"  {i}/{len(cases)}", flush=True)
            json.dump({"preds": preds}, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump({"preds": preds}, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("done", dst)


if __name__ == "__main__":
    main(*(sys.argv[1:] or []))
