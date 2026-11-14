"""Generate 100 realistically structured clinical notes (for gold annotation).

结构：主诉 + 现病史 + 既往史/用药 + 查体 + 辅助检查 + 诊断 + 治疗。
保证临床合理性（产科只给孕妇、男性不给妇科、儿科用"患儿"）。
"""
import json
import random

random.seed(20261108)

SYMPTOMS = [
    ("发热", "fever"), ("咳嗽", "cough"), ("咳痰", "productive cough"), ("气促", "dyspnea"),
    ("胸痛", "chest pain"), ("胸闷", "chest tightness"), ("心悸", "palpitation"), ("头痛", "headache"),
    ("头晕", "dizziness"), ("恶心", "nausea"), ("呕吐", "vomiting"), ("腹痛", "abdominal pain"),
    ("腹泻", "diarrhea"), ("便秘", "constipation"), ("乏力", "fatigue"), ("水肿", "edema"),
    ("反酸", "acid reflux"), ("嗳气", "burping"), ("失眠", "insomnia"), ("视物模糊", "blurred vision"),
]
DISEASES_COMMON = [
    ("高血压", "essential hypertension"), ("2型糖尿病", "type 2 diabetes mellitus"),
    ("冠心病", "coronary arteriosclerosis"), ("肺炎", "pneumonia"), ("哮喘", "asthma"),
    ("胃溃疡", "gastric ulcer"), ("缺铁性贫血", "iron deficiency anemia"), ("心房颤动", "atrial fibrillation"),
    ("慢性心力衰竭", "congestive heart failure"), ("慢性肾脏病", "chronic kidney disease"),
    ("脑梗死", "cerebral infarction"), ("甲状腺功能亢进", "hyperthyroidism"),
]
DISEASES_OB = [("子痫前期", "pre-eclampsia"), ("妊娠期糖尿病", "gestational diabetes")]
DISEASES_PED = [("支气管肺炎", "bronchopneumonia"), ("手足口病", "hand foot and mouth disease")]
DRUGS = [
    ("氨氯地平", "amlodipine"), ("二甲双胍", "metformin"), ("阿司匹林", "aspirin"),
    ("奥美拉唑", "omeprazole"), ("布洛芬", "ibuprofen"), ("头孢曲松", "ceftriaxone"),
    ("胰岛素", "insulin"), ("呋塞米", "furosemide"),
]
VITALS = [
    ("血压", "blood pressure", ["150/95mmHg", "160/100mmHg", "130/80mmHg", "165/110mmHg"]),
    ("心率", "heart rate", ["92次/分", "88次/分", "102次/分", "76次/分"]),
    ("体温", "body temperature", ["38.5℃", "37.8℃", "36.9℃", "39.2℃"]),
]
EXAMS = [
    ("双肺湿啰音", "respiratory crackles"), ("双肺哮鸣音", "wheezing"), ("上腹压痛", "abdominal tenderness"),
    ("双下肢水肿", "edema"), ("咽部充血", "pharyngeal congestion"), ("肝脾未触及", None),
]
PROCS = [
    ("心电图", "electrocardiographic procedure"), ("胸部CT", "CT of chest"),
    ("腹部超声", "ultrasonography"), ("胃镜", "esophagogastroduodenoscopy"), ("头颅CT", "CT of head"),
]
TESTS = [
    ("白细胞", "leukocyte count", "12.5×10^9/L", "全血"),
    ("血红蛋白", "hemoglobin", "88g/L", "全血"),
    ("肌酐", "creatinine", "186μmol/L", "血清"),
    ("空腹血糖", "fasting blood glucose", "13.5mmol/L", "血清"),
    ("C反应蛋白", "C reactive protein", "45mg/L", "血清"),
]
TEST_GOLD = {
    "白细胞": ("leukocyte count", "3000905"),
    "血红蛋白": ("hemoglobin measurement", "3000963"),
    "肌酐": ("creatinine measurement", "4324383"),
    "空腹血糖": ("fasting blood glucose measurement", "4156660"),
    "C反应蛋白": ("C-reactive protein measurement", "4208414"),
}
DURS = ["2天", "3天", "1周", "2周", "1个月", "3个月", "半年"]


def pick(pool, n=1):
    return random.sample(pool, min(n, len(pool)))


def gen_note(i):
    """Return (text, list of fact dicts)."""
    sex = "男" if i % 2 == 0 else "女"
    age = random.choice([25, 30, 34, 45, 52, 58, 65, 70, 78])
    is_preg = (sex == "女" and 20 <= age <= 40 and i % 7 == 0)
    is_ped = age <= 14
    subj = "患儿" if is_ped else "患者"

    obs = []  # facts

    # chief complaint
    s1 = random.choice(SYMPTOMS)
    dur = random.choice(DURS)
    obs.append({"span": s1[0], "en": s1[1], "domain": "Condition", "assertion": "肯定", "subject": subj})

    # history of present illness: second symptom + optionally negated third
    s2 = random.choice([s for s in SYMPTOMS if s[0] != s1[0]])
    obs.append({"span": s2[0], "en": s2[1], "domain": "Condition", "assertion": "肯定", "subject": subj})
    s3 = random.choice([s for s in SYMPTOMS if s[0] not in (s1[0], s2[0])])
    neg = random.random() < 0.5
    obs.append({"span": s3[0], "en": s3[1], "domain": "Condition",
                "assertion": "否定" if neg else "肯定", "subject": subj})

    # past history + medication
    if is_preg:
        d = random.choice(DISEASES_OB)
    elif is_ped:
        d = random.choice(DISEASES_PED)
    else:
        d = random.choice(DISEASES_COMMON)
    obs.append({"span": d[0], "en": d[1], "domain": "Condition", "assertion": "既往", "subject": subj})
    dr = random.choice(DRUGS)
    obs.append({"span": dr[0], "en": dr[1], "domain": "Drug", "assertion": "服用中", "subject": subj})

    # physical exam: one vital sign + one finding
    v = random.choice(VITALS)
    val = random.choice(v[2])
    if v[0] == "血压" and "/" in val:
        sv, dv = val.replace("mmHg", "").split("/")
        obs.append({"span": "收缩压", "en": "systolic blood pressure", "domain": "Measurement",
                    "assertion": "肯定", "value": sv + "mmHg", "subject": subj})
        obs.append({"span": "舒张压", "en": "diastolic blood pressure", "domain": "Measurement",
                    "assertion": "肯定", "value": dv + "mmHg", "subject": subj})
    else:
        obs.append({"span": v[0], "en": v[1], "domain": "Measurement", "assertion": "肯定", "value": val, "subject": subj})
    ex = random.choice(EXAMS)
    if ex[1]:
        span, site, site_en = {
            "双肺湿啰音": ("湿啰音", "双肺", "both lungs"),
            "双肺哮鸣音": ("哮鸣音", "双肺", "both lungs"),
            "上腹压痛": ("压痛", "上腹", "epigastrium"),
            "双下肢水肿": ("水肿", "双下肢", "both lower extremities"),
            "咽部充血": ("充血", "咽部", "pharynx"),
        }[ex[0]]
        obs.append({"span": span, "en": ex[1], "domain": "Condition", "assertion": "肯定",
                    "subject": subj, "site": site, "site_en": site_en})

    # auxiliary exams (site split out of the span)
    p = random.choice(PROCS)
    span, site, site_en, en = {
        "心电图": ("心电图", None, None, "electrocardiographic procedure"),
        "胸部CT": ("CT", "胸部", "chest", "computed tomography"),
        "腹部超声": ("超声", "腹部", "abdomen", "ultrasonography"),
        "胃镜": ("胃镜", None, None, "esophagogastroduodenoscopy"),
        "头颅CT": ("CT", "头颅", "head", "computed tomography"),
    }[p[0]]
    obs.append({"span": span, "en": en, "domain": "Procedure", "assertion": "检查",
                "subject": subj, "site": site, "site_en": site_en})
    t = random.choice(TESTS)
    span, en_gold, gold_cid = t[0], TEST_GOLD[t[0]][0], TEST_GOLD[t[0]][1]
    obs.append({"span": span, "en": en_gold, "domain": "Measurement", "assertion": "肯定",
                "value": t[2], "specimen": t[3], "subject": subj,
                "concept_id": int(gold_cid) if gold_cid.isdigit() else None})

    # diagnosis (reuse the past disease or a new one)
    dx = d if random.random() < 0.5 else random.choice(DISEASES_PED if is_ped else DISEASES_COMMON)
    obs.append({"span": dx[0], "en": dx[1], "domain": "Condition", "assertion": "肯定", "subject": subj})

    text = (
        f"{subj}{sex}，{age}岁，因{s1[0]}{dur}就诊。"
        f"伴{s2[0]}，{'无' if neg else '有'}{s3[0]}。"
        f"既往{d[0]}，长期服用{dr[0]}。"
        f"查体：{v[0]}{val}" + (f"，{ex[0]}。" if ex[1] else "。") +
        f"{p[0]}示异常，{t[0]}{t[2]}。"
        f"诊断：{dx[0]}。"
    )
    return text, obs


def main(n=100):
    cases, seen = [], set()
    i = 0
    while len(cases) < n:
        text, obs = gen_note(i)
        i += 1
        if text in seen:
            continue
        seen.add(text)
        cases.append({"id": f"R{len(cases)+1:03d}", "text": text, "mentions": obs})
    json.dump({"cases": cases}, open("cases_real100.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"generated {len(cases)} realistic notes; facts={sum(len(c['mentions']) for c in cases)}")


if __name__ == "__main__":
    main()
