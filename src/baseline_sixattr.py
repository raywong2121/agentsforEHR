# -*- coding: utf-8 -*-
"""B1 基线：同六属性输出协议的直接生成（不访问词表）。

同一模型、同一输入，一次生成即输出六属性 + 凭模型记忆直接给出 concept_id。
与主系统唯一的不同：概念不经过词表检索/选择/自检。用于区分
"输出协议的结构性差距" 与 "检索-反馈带来的概念命中差距"。
"""
import json
import os
import re
import sys
import time

import pipeline
from pipeline import llm

B1_SYS = (
    "You are a clinical concept coding assistant for OMOP. "
    "From the Chinese clinical text, extract every codable clinical concept and output a JSON array. "
    "Each element must be: "
    '{"span":"core concept word","zh":"Chinese concept","concept_id": <the OMOP standard concept_id, '
    'answered from your own knowledge WITHOUT any vocabulary lookup>,'
    '"assertion":"肯定/否定/疑似/既往/服用中/检查","value":"number or null",'
    '"specimen":"全血/血清/血浆/尿/null","subject":"患者/胎儿/患者家属","site":"body site or null"}. '
    "Rules: split blood pressure into systolic and diastolic records; put negation/status/time into assertion; "
    "numeric values into value; keep composite names whole; site goes into the site field only; "
    "if you do not know the concept_id, output -1."
)


def run_case(text: str) -> dict:
    prompt = f"文本：{text}\n只输出 JSON 数组。"
    raw = llm([{"role": "system", "content": B1_SYS}, {"role": "user", "content": prompt}],
              max_tokens=1600, json_mode=True)
    try:
        mentions = pipeline._normalize(json.loads(raw))
    except (ValueError, TypeError):
        mentions = []
    rows = []
    for m in mentions:
        cid_raw = m.get("concept_id", -1)
        try:
            cid = int(cid_raw)
        except (TypeError, ValueError):
            cid = None
        mapped = isinstance(cid, int) and cid > 0
        rows.append({
            "span": m.get("span", ""), "zh": m.get("zh", ""),
            "concept_id": cid if mapped else None,
            "concept_name": str(cid) if mapped else "",
            "status": "mapped" if mapped else "unmapped",
            "assertion": m.get("assertion", ""), "value": m.get("value"),
            "specimen": m.get("specimen"), "subject": m.get("subject", ""),
            "site": m.get("site"), "site_en": None, "site_concept_id": None,
            "domain": "", "ens": [],
            "reason": "direct generation (no vocabulary)" if mapped else "direct generation: unknown id",
        })
    return {"text": text, "rows": rows}


def main():
    cases_path = sys.argv[1] if len(sys.argv) > 1 else "cases_fold1.json"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "preds_fold1_b1.json"
    cases = json.load(open(cases_path, encoding="utf-8"))["cases"]
    preds, errors = [], []
    t0 = time.time()
    for i, c in enumerate(cases, 1):
        try:
            res = run_case(c["text"])
            res["id"] = c["id"]
            preds.append(res)
            print(f"[{i:02d}/{len(cases)}] {c['id']} mentions={len(res['rows'])}", flush=True)
        except Exception as e:
            errors.append({"id": c["id"], "error": str(e)[:200]})
            preds.append({"id": c["id"], "text": c["text"], "rows": [], "error": str(e)[:200]})
            print(f"[{i:02d}/{len(cases)}] {c['id']} ERROR {str(e)[:120]}", flush=True)
        json.dump({"preds": preds, "errors": errors}, open(out_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    print(f"\ndone in {time.time()-t0:.1f}s; cases={len(preds)} errors={len(errors)}", flush=True)


if __name__ == "__main__":
    main()
