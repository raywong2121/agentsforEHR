# -*- coding: utf-8 -*-
"""生成盲裁定任务：给另一个模型用的自包含任务文档（Markdown 可粘贴 + JSON 可编程调用）。

关键设计：候选池 = 系统前3候选 ∪ 旧金标准，按名称排序，不标注角色，保证裁定盲化。
"""
import json

data = json.load(open("adjudication_data.json", encoding="utf-8"))
sus, facts, cases = data["sus"], data["facts"], data["cases"]

items = []
seen_keys = set()
for s in sus:
    key = (s["id"], s["span"])
    if key in seen_keys:
        continue
    seen_keys.add(key)
    pool_ids = []
    for t in s["top3"]:
        cid = int(t.rsplit("(", 1)[1].rstrip(")"))
        if cid not in pool_ids:
            pool_ids.append(cid)
    if s.get("gold_id") and s["gold_id"] not in pool_ids:
        pool_ids.append(s["gold_id"])
    pool = []
    for cid in pool_ids:
        f = facts.get(str(cid))
        if not f:
            continue
        pool.append({"concept_id": cid, "name": f["name"], "domain": f["dom"],
                     "vocabulary": f["voc"], "class": f["cc"],
                     "standard": f["std"] == "S"})
    pool.sort(key=lambda x: x["name"].lower())
    items.append({"id": s["id"], "span": s["span"],
                  "text": cases.get(s["id"], {}).get("text", ""),
                  "pool": pool})

json.dump(items, open("adjudication_task.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

RULES = """【任务】
下面有 36 个中文临床病历片段。对每个片段，从候选概念池中选出最合适的 OMOP/OHDSI 标准概念。
这是"盲裁定"：候选池的顺序已打乱，不要推测哪个概念来自哪个系统。

【裁定规则】
1. 只依据病历原文判断片段的含义。不得补出原文未提及的病因、部位、严重程度、标本类型、分期。
2. 优先选 standard=true 的概念。
3. 若片段含部位/标本/程度限定（如"腹部""血清"），选包含该限定的概念；若片段没有限定，则不选含额外限定的概念。
4. 若多个候选临床含义等价（仅粒度不同），允许标注多个"可接受"，并给出一个首选。
5. 若池中没有合适概念，输出"弃权"并写一句话理由。
6. 判断时不要受概念名长度、首字母影响。

【输出格式】
对每个条目输出 JSON 对象：
{"item": 1, "pick": 首选concept_id或null, "acceptable": [其他可接受concept_id], "verdict": "pick/abstain", "reason": "一句话理由"}
最后输出一个 JSON 数组，包含全部 36 个条目。只输出 JSON，不要解释。

"""

md = ["# 中文临床概念编码：盲裁定任务", "", RULES, ""]
for k, it in enumerate(items, 1):
    md.append(f"### 条目 {k}（{it['id']}）")
    md.append(f"病历原文：{it['text']}")
    md.append(f"待编码片段：{it['span']}")
    md.append("候选概念池：")
    for c in it["pool"]:
        std = "标准" if c["standard"] else "非标准"
        md.append(f"- [{c['concept_id']}] {c['name']}（{c['domain']}｜{c['vocabulary']}｜{c['class']}｜{std}）")
    md.append("")

import os as _os
open(_os.environ.get("BLIND_TASK_MD", "盲裁定任务_36条.md"), "w", encoding="utf-8").write("\n".join(md))
print(f"items: {len(items)} | candidates avg: {sum(len(i['pool']) for i in items)/len(items):.1f}")
print("saved: 盲裁定任务_36条.md, adjudication_task.json")
