# -*- coding: utf-8 -*-
"""最终裁定（v2）：修正匹配逻辑与两处裁定，输出给用户校核的 Excel。"""
import json
import os
from collections import Counter

RULINGS = {
    "乏力": dict(pick=4223659, acceptable=[437113],
                 reason="'乏力'无肌肉等限定，选 Fatigue；Asthenia 为近义可接受；Muscle fatigue 增加原文没有的限定，排除。"),
    "慢性心力衰竭": dict(pick=444031, acceptable=[],
                 reason="'慢性心力衰竭'直译 Chronic heart failure；Congestive 增加'充血性'病理类型、Decompensated/Exacerbation 增加失代偿/加重，原文均无，排除。"),
    "白细胞": dict(pick=4298431, acceptable=[37208925],
                 reason="'白细胞12.5×10^9/L'为白细胞计数，选 White blood cell count；'in blood' 标本限定为常规默认，可接受；Polymorphonuclear 与 CSF 的限定原文均无，排除。"),
    "冠心病": dict(pick=317576, acceptable=[],
                 reason="'冠心病'直译 Coronary arteriosclerosis；Left main / Premature 增加原文没有的限定，排除。"),
    "腹部超声": dict(pick=4261497, acceptable=[2793111, 3014650],
                 reason="原文含部位'腹部'，应选含部位的概念；SNOMED Ultrasonography of abdomen 首选，ICD10PCS 同名与 US Abdomen 可接受；通用 Ultrasonography 丢部位，排除。"),
    "压痛": dict(pick=4082445, acceptable=[197981],
                 reason="按部位分离约定：概念=Tenderness，部位'上腹'单独编码为 Epigastric region(4101855)；复合概念 Abdominal tenderness 亦表达同一事实，可接受。"),
    "C反应蛋白": dict(pick=4208414, acceptable=[4048092],
                 reason="'C反应蛋白45mg/L'为检验结果，选 C-reactive protein measurement；CRP gene variant 属基因变异检测，排除；Serum level 若默认血清化验亦可接受。"),
    "血红蛋白": dict(pick=None, acceptable=[], abstain=True,
                 reason="池内无'血红蛋白浓度测量'标准概念：HEMOGLOBIN 为药物成分、Hemoglobin G level 为 G 变异型、Measurement 过泛。属词表召回缺口。"),
    "咳痰": dict(pick=4102774, acceptable=[],
                 reason="'咳痰'为咳嗽伴痰的症状，选 Productive cough；排痰操作、痰液实体、'增多'限定均不符原文，排除。"),
    "肌酐": dict(pick=4324383, acceptable=[4013964],
                 reason="'肌酐186μmol/L'为肌酐测定，选 Creatinine measurement；'serum' 变体为默认标本亦可接受；Creatinine Value 属 NAACCR 癌症登记变量、creatinine 为药物成分，排除。"),
}


def main():
    items = json.load(open("adjudication_task.json", encoding="utf-8"))
    gold_map = {}
    for g in json.load(open("gold_real100.json", encoding="utf-8"))["gold"]:
        for m in g["mentions"]:
            if m.get("concept_id"):
                gold_map[(g["id"], m["span"])] = m["concept_id"]
    pred_map = {}
    for p in json.load(open("preds_fold1_logged.json", encoding="utf-8"))["preds"]:
        for r in p.get("rows", []):
            pred_map[(p["id"], r["span"])] = r

    def find(key, table):
        if key in table:
            return table[key]
        for (cid, span), v in table.items():
            if cid == key[0] and (key[1] in span or span in key[1]):
                return v
        return None

    rows = []
    for it in items:
        r = RULINGS[it["span"]]
        pick = r["pick"]
        pool_ids = [c["concept_id"] for c in it["pool"]]
        if pick is not None and pick not in pool_ids:
            pick = None
        abstain = bool(r.get("abstain")) or (pick is None)
        reason = r["reason"]

        # 金标准里的 mention（可能带部位）：上腹压痛 vs 行里的 压痛
        gkey = next(((gid, ms) for (gid, ms) in gold_map if gid == it["id"]
                     and (ms == it["span"] or ms.endswith(it["span"]) or it["span"] in ms)), None)
        old_gold = gold_map.get(gkey) if gkey else None
        prow = find((it["id"], it["span"]), pred_map)
        old_pred = prow.get("concept_id") if prow and prow.get("status") == "mapped" else None
        site = (prow or {}).get("site")
        site_cid = (prow or {}).get("site_concept_id")

        sys_ok = old_pred in ([pick] + r["acceptable"])
        g_ok = old_gold in ([pick] + r["acceptable"])
        if pick is None:
            sys_ok = g_ok = False
        if sys_ok and not g_ok:
            label = "系统对"
        elif g_ok and not sys_ok:
            label = "金标准对"
        elif sys_ok and g_ok:
            label = "都对"
        else:
            label = "都错/待改"

        rows.append({
            "id": it["id"], "span": it["span"], "site": site or "", "text": it["text"],
            "pick": pick, "acceptable": r["acceptable"], "abstain": abstain, "reason": reason,
            "old_gold": old_gold, "old_pred": old_pred, "site_cid": site_cid, "label": label,
        })

    json.dump(rows, open("rulings.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("标签分布:", dict(Counter(x["label"] for x in rows)), "| 弃权:", sum(x["abstain"] for x in rows))

    # 生成校核 Excel
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook(); ws = wb.active; ws.title = "裁定校核"
    headers = ["编号", "病历原文", "待编码事实", "系统输出", "旧金标准", "我的裁定", "理由", "三方对比", "你的校核", "备注"]
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4F81BD")
    name_of = {}
    for c in json.load(open("adjudication_data.json", encoding="utf-8"))["facts"].items():
        cid = int(c[0])
        name_of[cid] = c[1]["name"]
    for i, x in enumerate(rows, start=2):
        mention = f"{x['span']}" + (f"（{x['site']}）" if x["site"] else "")
        pred = (f"{name_of.get(x['old_pred'], x['old_pred'])} ({x['old_pred']})" if x["old_pred"] else "弃权")
        gg = (f"{name_of.get(x['old_gold'], x['old_gold'])} ({x['old_gold']})" if x["old_gold"] else "—")
        mine = ("弃权" if x["abstain"] else f"{name_of.get(x['pick'], x['pick'])} ({x['pick']})")
        if not x["abstain"] and x["acceptable"]:
            acc = "; ".join(f"{name_of.get(a, a)}" for a in x["acceptable"])
            mine += f"\n可接受：{acc}"
        ws.cell(row=i, column=1, value=x["id"])
        ws.cell(row=i, column=2, value=x["text"]).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=i, column=3, value=mention)
        ws.cell(row=i, column=4, value=pred)
        ws.cell(row=i, column=5, value=gg)
        ws.cell(row=i, column=6, value=mine).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=i, column=7, value=x["reason"]).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=i, column=8, value=x["label"])
        ws.cell(row=i, column=9, value="")
        ws.cell(row=i, column=10, value="")
    dv = DataValidation(type="list", formula1='"同意,不同意"', allow_blank=True)
    ws.add_data_validation(dv); dv.add(f"I2:I{len(rows)+1}")
    for col, w in {"A": 7, "B": 45, "C": 12, "D": 26, "E": 26, "F": 30, "G": 40, "H": 10, "I": 10, "J": 18}.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    out_path = os.environ.get("ADJUDICATION_XLSX", "裁定校核_31条.xlsx")
    wb.save(out_path)
    print("saved 裁定校核_31条.xlsx")


if __name__ == "__main__":
    main()
