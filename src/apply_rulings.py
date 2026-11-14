# -*- coding: utf-8 -*-
"""把最终裁定落进新金标准（覆盖生成器自动检索仍会引入的偏差）。"""
import json

FIX = {
    "乏力": dict(cid=4223659, alt=[437113]),
    "慢性心力衰竭": dict(cid=444031, alt=[]),
    "白细胞": dict(cid=3000905, alt=[4298431, 37208925]),
    "冠心病": dict(cid=317576, alt=[]),
    "超声": dict(cid=4037672, alt=[4261497, 2793111, 3014650]),
    "CT": dict(cid=4300757, alt=[]),
    "压痛": dict(cid=4082445, alt=[197981]),
    "C反应蛋白": dict(cid=4208414, alt=[4048092]),
    "血红蛋白": dict(cid=3000963, alt=[4153000]),
    "咳痰": dict(cid=4102774, alt=[]),
    "肌酐": dict(cid=4324383, alt=[4013964]),
    "胰岛素": dict(cid=35819838, alt=[]),
    "充血": dict(cid=4146766, alt=[]),
    "收缩压": dict(cid=3004249, alt=[]),
    "舒张压": dict(cid=3012888, alt=[]),
}


def main():
    gold = json.load(open("gold_real100.json", encoding="utf-8"))
    n = 0
    for g in gold["gold"]:
        for m in g["mentions"]:
            for span, fix in FIX.items():
                if m["span"] == span or span in m["span"] or m["span"] in span:
                    if m.get("concept_id") != fix["cid"]:
                        n += 1
                    m["concept_id"] = fix["cid"]
                    m["alt_ids"] = fix["alt"] or None
                    break
    json.dump(gold, open("gold_real100.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    f1 = {f"R{i:03d}" for i in range(1, 21)}
    sub = {"gold": [g for g in gold["gold"] if g["id"] in f1]}
    json.dump(sub, open("gold_fold1_v2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("gold mentions adjusted:", n)


if __name__ == "__main__":
    main()
