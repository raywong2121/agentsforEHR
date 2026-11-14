"""临床文本 -> OHDSI 标准概念 编码管线（抽取 → 检索-重述循环 → 选择 → 覆盖校验 → 输出）。

设计原则：不靠词库查表。中文→标准概念的桥，由"模型给多候选 + 检索反馈 + 重述"的
循环策略完成（agentic search）。词表只做确定性检索。

模型调用走 Dify 控制台（登录态 + model_config 覆盖），或直连 DeepSeek API（有 key 时）。
"""
import json
import os
import re
import urllib.request

import flash
from retrieve import retrieve, retrieve_cn

DOMAINS = ("Condition", "Drug", "Measurement", "Observation", "Procedure", "Spec Anatomic Site")

EXTRACT_SYS = (
    "你是临床文本概念编码助手。任务：从中文临床文本中抽出**可独立编码为 OMOP 标准概念的临床概念词**，"
    "并为每个概念给出 1-3 个英文候选术语（ens 数组）。\n"
    "【只抽取】症状、疾病/诊断、药物、检查/测量项目、关键解剖部位。\n"
    "【域】疼痛/症状/疾病→Condition；药物→Drug；检验/生命体征数值→Measurement；检查项目→Procedure；解剖部位→Spec Anatomic Site。\n"
    "【部位】解剖部位修饰（双下肢/右侧/双肺/咽部/上腹）不并入概念词、不单独编码："
    "site 写中文原文，site_en 写对应英文解剖术语（如 双肺→both lungs，双下肢→both lower extremities，"
    "上腹→epigastrium，咽部→pharynx，右肩→right shoulder，头颅→head，胸部→chest，腹部→abdomen）；"
    "无明确解剖概念的方位词（右侧/左侧）site_en 置 null。\n"
    "【核心名词】span 只保留最核心概念名词：'双下肢水肿'→span'水肿'+site'双下肢'；'规律宫缩'→span'宫缩'+assertion'规律'；"
    "'血肌酐'→span'肌酐'；'胎心基线'→span'胎心'。\n"
    "【复合全名保持完整】糖化血红蛋白、糖尿病肾病、子痫前期、支气管肺炎、蛛网膜下腔出血等是独立概念名，不要拆成更短的词。\n"
    "【绝不单独抽取，写进字段】：否定/状态/时间/程度/频率词→assertion；数值与单位→value；动词（服用/停用/复查）→并入 assertion；"
    "主语（患者/胎儿/家属）→subject。\n"
    "【血压拆分】血压为两个数值（如 165/110mmHg）时，拆为两条：span='收缩压' value='165'，span='舒张压' value='110'；"
    "只有一个数值时才用 span='血压'。\n"
    "【标本】检验项目写 specimen：血常规（白细胞/血红蛋白）→全血；血生化（肌酐/血糖/C反应蛋白）→血清；尿检（尿酮体）→尿；"
    "无明确标本置 null。\n"
    "【检查项目断言】检查项目（心电图/CT/超声/胃镜/穿刺）被执行时，assertion 统一写 '检查'，不要写 '肯定'。\n"
    "【概念名已含部位】概念名本身已含部位/器官或部位语素的（胃溃疡、肺炎、脑梗死、冠心病、胸痛、胸闷、腹痛、头痛、"
    "腰痛、关节痛等），不要再写 site。\n"
    "【检查项目拆部位】'头颅CT'→span'CT'+site'头颅'，'胸部CT'→span'CT'+site'胸部'，'腹部超声'→span'超声'+site'腹部'，"
    "'腹部CT'→span'CT'+site'腹部'；部位一律进 site，不并入 span。\n"
    "【英文 ens】每个概念给 1-3 个英文临床术语，**首选最具体的标准名**，不要只给上位词/俗称："
    "'高血压'→[\"essential hypertension\",\"hypertension\"]；'反酸'→[\"acid reflux\",\"heartburn\"]；"
    "'糖尿病酮症'→[\"diabetic ketoacidosis\",\"ketosis\"]；'胸闷'→[\"chest tightness\",\"chest discomfort\"]。\n"
    "【示例】'患者今日开始服用二甲双胍' → 仅 [{\"span\":\"二甲双胍\",\"assertion\":\"服用中\",\"ens\":[\"metformin\"]}]，'服用'不抽取。"
)

REFORMULATE_PROMPT = (
    "临床片段「{span}」（中文概念：{zh}，应属 {domain} 域）。\n"
    "已尝试这些英文术语，但在 OMOP 词表里都没命中标准概念：{tried}。\n"
    "请换 2-3 个不同的英文临床术语（同义词、上位词、或 OMOP 常用表述），只输出 JSON 数组，如 [\"a\",\"b\"]。"
)


def llm(messages, max_tokens=500, json_mode=False):
    # 有直连 API key 时优先；否则走 Dify 控制台
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
            method="POST")
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r)["choices"][0]["message"]["content"]

    system = next((m["content"] for m in messages if m["role"] == "system"), "你是助手。")
    user = next((m["content"] for m in messages if m["role"] == "user"), "")
    return flash.chat(system, user, max_tokens=max_tokens, json_mode=json_mode)["text"]


def _normalize(value):
    """把模型可能返回的多种形状统一成 [dict]。"""
    if isinstance(value, dict):
        for key in ("data", "mentions", "concepts", "items", "result"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
        else:
            value = []
    if not isinstance(value, list):
        return []
    out = []
    for m in value:
        if isinstance(m, str):
            out.append({"span": m.strip(), "zh": m.strip()})
        elif isinstance(m, dict) and (m.get("span") or m.get("zh")):
            out.append(m)
    return out


def extract(text: str) -> list[dict]:
    prompt = (
        "把下面的临床文本抽出可独立编码的临床概念，只输出一个 JSON 数组，每个元素：\n"
        '{"span":"核心概念词","zh":"中文核心概念","ens":["英文术语1","英文术语2"],"site":"部位原文或null","site_en":"部位英文或null","domain":"'
        + "/".join(DOMAINS)
        + '","assertion":"肯定/否定/疑似/既往/停用中/已缓解","subject":"患者/胎儿/患者家属","value":"数值或null","specimen":"全血/血清/血浆/尿/null"}\n'
        "只输出可编码的临床概念词；否定/程度/频率/数值/动词/主语一律写进字段；部位写进 site；"
        "复合检验/疾病全名保持完整；span 是原文连续子串且只含核心概念词本身。\n"
        f"文本：{text}"
    )
    msgs = [{"role": "system", "content": EXTRACT_SYS}, {"role": "user", "content": prompt}]
    raw = llm(msgs, max_tokens=1600, json_mode=True)
    try:
        return _normalize(json.loads(raw))
    except (ValueError, TypeError):
        raw = llm(msgs + [{"role": "user", "content": "只输出 JSON 数组，不要任何解释或代码围栏。"}],
                  max_tokens=1600, json_mode=True)
        return _normalize(json.loads(raw))


def _merge(cands_list, domain):
    """合并候选；优先级：同域 > 精确/同义(src小) > 模型给的术语顺序(term_idx小)。"""
    merged = {}
    for cands in cands_list:
        for c in cands:
            k = c["concept_id"]
            if k not in merged or (int(c["src"]), int(c.get("term_idx", 99))) < (
                    int(merged[k]["src"]), int(merged[k].get("term_idx", 99))):
                merged[k] = c
    # 排序：强匹配(src<=2)优先；同档内按模型术语顺序；再按 src/同域/名字长度
    return sorted(merged.values(), key=lambda x: (
        0 if int(x["src"]) <= 2 else 1, int(x.get("term_idx", 99)), int(x["src"]),
        0 if (domain and x["domain"] == domain) else 1, len(x["concept_name"])))


def gather(span: str, zh: str, ens, domain: str | None, max_rounds: int | None = None) -> list[dict]:
    """检索-反馈-重述循环：多英文候选检索；无精确命中时让模型换术语再检索。"""
    if max_rounds is None:
        max_rounds = int(os.environ.get("GATHER_ROUNDS", "2"))
    terms = [t for t in (ens or []) if isinstance(t, str) and t.strip()]
    tried = set()
    pools = []
    for rnd in range(max_rounds + 1):
        for t_idx, t in enumerate(terms):
            key = t.strip().lower()
            if not key or key in tried:
                continue
            tried.add(key)
            got = retrieve(t.strip(), domain)
            if not got or int(got[0]["src"]) > 2:
                got = got + retrieve(t.strip(), None)   # 合并全域，同域优先留给 _merge
            for c in got:
                c["term_idx"] = t_idx                    # 记录模型候选顺序
            pools.append(got)
        cands = _merge(pools, domain)
        if cands and int(cands[0]["src"]) <= 2:   # 已有精确/同义命中，收工
            return cands
        if rnd >= max_rounds:
            break
        # 反馈重述：把"没命中"喂回模型，让它换一组英文术语
        tried_txt = ", ".join(sorted(tried)) or "(无)"
        prompt = REFORMULATE_PROMPT.format(span=span, zh=zh, domain=domain or "临床", tried=tried_txt)
        try:
            new_terms = json.loads(llm([{"role": "user", "content": prompt}], max_tokens=80, json_mode=True))
            new_terms = new_terms.get("data") if isinstance(new_terms, dict) else new_terms
        except Exception:
            break
        if not isinstance(new_terms, list) or not [t for t in new_terms if isinstance(t, str)]:
            break
        terms = [t for t in new_terms if isinstance(t, str)]
    return _merge(pools, domain)


def select(span: str, zh: str, ens, cands: list[dict], domain: str | None = None,
           limit: int = 8, lessons: str = "") -> int | None:
    """从候选中选唯一 concept_id，或弃权(None)。仅在无精确/同义命中时调用。"""
    if not cands:
        return None
    if int(cands[0]["src"]) <= 2 and not lessons:   # 有经验时必须走模型判断
        return cands[0]["concept_id"]
    cands = cands[:limit]
    menu = "\n".join(f"{i}: {c['concept_id']} {c['concept_name']} ({c['domain']})" for i, c in enumerate(cands))
    hint = f"，应属 {domain} 域，优先同域候选" if domain else ""
    exp_block = ""
    if lessons:
        exp_block = lessons + "\n**若上述历史经验明确指出了该选哪个概念，优先遵循经验，即使其它候选是精确字面匹配。**\n\n"
    prompt = (
        exp_block
        + f"临床片段「{span}」（英文候选术语：{', '.join(ens or [])}{hint}）。候选标准概念：\n{menu}\n"
        + "选出语义最匹配的候选序号；只要候选中有语义相近的标准概念就选它（宁可选相近概念，不要轻易弃权），"
        "只有完全没有相关候选时才输出 -1。只输出一个数字。"
    )
    raw = llm([{"role": "user", "content": prompt}], max_tokens=10).strip().replace("```", "").strip()
    try:
        idx = int(raw)
    except ValueError:
        return None
    return cands[idx]["concept_id"] if 0 <= idx < len(cands) else None


VERIFY_PROMPT = (
    "临床片段「{span}」（中文概念：{zh}）。系统打算把它编码为：{name}（concept_id {cid}，{domain} 域）。\n"
    "请判断这个标准概念是否与片段语义一致。只输出一个词：\n"
    "match（语义一致，可采用）\n"
    "narrow（太窄/太具体，例如把'C反应蛋白'配到'基因变异检测'）\n"
    "broad（太宽/太泛）\n"
    "mismatch（不是同一个概念）"
)


def verify(span: str, zh: str, name: str, cid: int, domain: str | None) -> str:
    """自检：判断已选概念是否与原文片段语义一致。返回 match/narrow/broad/mismatch/unknown。"""
    prompt = VERIFY_PROMPT.format(span=span, zh=zh, name=name, cid=cid, domain=domain or "临床")
    try:
        raw = llm([{"role": "user", "content": prompt}], max_tokens=10)
    except Exception:
        return "unknown"
    out = raw.strip().lower().replace("```", "")
    for k in ("match", "narrow", "broad", "mismatch"):
        if k in out:
            return k
    return "unknown"


def reformulate_for_retry(span: str, zh: str, bad_name: str, verdict: str, domain: str | None) -> list[str]:
    """自检失败后：让模型换更合适的英文术语（闭环的修正步）。"""
    prompt = (
        f"临床片段「{span}」（中文概念：{zh}，应属 {domain or '临床'} 域）。\n"
        f"之前选的标准概念「{bad_name}」被判定为「{verdict}」（不匹配/粒度不对）。\n"
        "请给出 1-3 个更合适的英文临床术语，只输出 JSON 数组，如 [\"term\"]。"
    )
    try:
        raw = llm([{"role": "user", "content": prompt}], max_tokens=80, json_mode=True)
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("data", [])
        return [x for x in data if isinstance(x, str)]
    except Exception:
        return []


MODIFIERS = {"重度", "轻度", "中度", "慢性", "急性", "反复", "持续", "既往", "圈数未记录"}
UNMAPPABLE_SUBSTR = {"绕腿", "晚期减速", "胎心监护"}


def run_case(text: str) -> dict:
    mentions = extract_cached(text)
    rows = []
    for m in mentions:
        span = m.get("span", "")
        zh = m.get("zh", "") or span
        ens = m.get("ens") or ([m["en"]] if m.get("en") else [])
        domain = m.get("domain") if m.get("domain") in DOMAINS else None
        cid = name = None
        reason = ""
        if span in MODIFIERS or any(u in span for u in UNMAPPABLE_SUBSTR) or not ens:
            reason = "修饰词/属性/不可映射短语"
        else:
            if os.environ.get("NO_MEMORY") != "1":
                try:
                    import strategy as _st2
                    _tg = _st2.targets(span, zh, domain)
                    if _tg:
                        ens = list(dict.fromkeys(_tg + list(ens)))
                except Exception:
                    pass
            cands = gather(span, zh, ens, domain)          # 检索-重述循环
            cands_cn = retrieve_cn(span)                   # 中文直达只作兜底信号
            if os.environ.get("NO_MEMORY") == "1":
                _has_exp = False
            else:
                try:
                    import strategy as _st0
                    _has_exp = bool(_st0.relevant(span, zh, domain))
                except Exception:
                    _has_exp = False
            _domain_ok = (domain is None) or (cands and cands[0]["domain"] == domain)
            _top_src = int(cands[0]["src"]) if cands else 99
            if cands and _top_src <= 2 and _domain_ok and not _has_exp:
                cid, name = cands[0]["concept_id"], cands[0]["concept_name"]
            elif cands_cn and not cands:
                cid, name = cands_cn[0]["concept_id"], cands_cn[0]["concept_name"]
            elif cands:
                if os.environ.get("NO_MEMORY") == "1":
                    lessons = ""
                else:
                    try:
                        import strategy as _st
                        lessons = _st.as_prompt_block(_st.relevant(span, zh, domain))
                    except Exception:
                        lessons = ""
                cid = select(span, zh, ens, cands, domain, lessons=lessons)
                if cid is not None:
                    name = next((c["concept_name"] for c in cands if c["concept_id"] == cid), "")
                else:
                    reason = "无匹配候选(弃权)"
            else:
                reason = "候选不足"

            # ===== 闭环：自检 → 不对则修正重搜 → 再自检 → 仍不对则弃权 =====
            if os.environ.get("NO_VERIFY") == "1":
                _skip_verify = True
            else:
                _skip_verify = False
            if cid is not None and (_top_src > 1 or _has_exp) and not _skip_verify:
                verdict = verify(span, zh, name, cid, domain)
                if verdict not in ("match", "unknown"):
                    new_terms = reformulate_for_retry(span, zh, name, verdict, domain)
                    fixed = False
                    if new_terms:
                        cands2 = gather(span, zh, new_terms, domain)
                        cid2 = select(span, zh, new_terms, cands2, domain)
                        if cid2 is not None:
                            name2 = next((c["concept_name"] for c in cands2 if c["concept_id"] == cid2), "")
                            v2 = verify(span, zh, name2, cid2, domain)
                            if v2 in ("match", "unknown"):
                                cid, name, fixed = cid2, name2, True
                    if not fixed:
                        cid, name = None, None
                        reason = f"自检未通过({verdict})，已弃权"
        # 泛化裸域概念拒绝：最终结果不允许是裸域名词（Measurement/Procedure 等），换同域覆盖 ens 词元最多的候选
        if cid is not None and name and name.strip().lower() in {
                "measurement", "procedure", "condition", "observation", "drug",
                "spec anatomic site", "clinical finding", "substance", "imaging"}:
            _ens_toks = set()
            for _e in (ens or []):
                _ens_toks |= set(re.findall(r"[a-z0-9]+", _e.lower()))
            _ens_toks = {_x for _x in _ens_toks if len(_x) > 2}
            _best = None
            for _c in (cands or []):
                _cn = _c["concept_name"].strip().lower()
                if _cn in {"measurement", "procedure", "condition", "observation", "drug",
                           "spec anatomic site", "clinical finding", "substance", "imaging"}:
                    continue
                if any(_b in _cn for _b in ("gene variant", "genetic", "dna ", "rna ",
                                            "allele", "mutation")):
                    continue
                if domain and _c["domain"] != domain:
                    continue
                _tt = set(re.findall(r"[a-z0-9]+", _c["concept_name"].lower()))
                _score = (len(_ens_toks & _tt), -int(_c.get("src", 9)))
                if _score[0] > 0 and (_best is None or _score > _best[0]):
                    _best = (_score, _c)
            if _best:
                cid, name = _best[1]["concept_id"], _best[1]["concept_name"]
                reason = "泛化裸域概念已被具体概念替换"

        # 过弃权否决：确定性的最后防线——若最终弃权，但顶部候选覆盖原文核心英文词且同域，则接受
        if cid is None and cands:
            _top = cands[0]
            if int(_top.get("src", 9)) <= 2 and (domain is None or _top["domain"] == domain):
                cid, name = _top["concept_id"], _top["concept_name"]
                reason = "弃权被否决（顶部候选为同域强匹配）"

        # 部位映射到解剖学标准概念（Spec Anatomic Site）
        site_txt = m.get("site")
        site_en = m.get("site_en")
        site_cid, site_name = None, ""
        if site_en:
            try:
                sc = retrieve(site_en, "Spec Anatomic Site")
                if sc and int(sc[0]["src"]) <= 3:
                    site_cid, site_name = sc[0]["concept_id"], sc[0]["concept_name"]
            except Exception:
                pass
        rows.append({
            "span": span, "zh": zh,
            "assertion": m.get("assertion", ""), "subject": m.get("subject", ""),
            "site": site_txt, "site_en": site_en,
            "site_concept_id": site_cid, "site_concept_name": site_name,
            "value": m.get("value"), "specimen": m.get("specimen"), "domain": domain,
            "concept_id": cid, "concept_name": name or "",
            "status": "mapped" if cid else "unmapped",
            "reason": reason,
            # 失败模式诊断所需：当时的候选集与英文候选词
            "candidates": [{"id": c["concept_id"], "name": c["concept_name"],
                            "src": int(c["src"]), "domain": c["domain"]}
                           for c in (cands or [])[:10]],
            "ens": list(ens or []),
        })
    # 去重（ASCII 后缀子串）+ 部位限定词剔除
    kept = []
    for r in rows:
        dup = any(r is not o and r["span"] != o["span"] and o["span"].endswith(r["span"])
                  and r["span"].isascii() and len(r["span"]) <= 3 for o in rows)
        if dup or r["domain"] == "Spec Anatomic Site":
            continue
        kept.append(r)
    return {"text": text, "rows": kept}


def render(result: dict) -> str:
    lines = [f"\n### {result['text']}\n",
             "| span | 概念 | concept_id | 域 | 部位 | 部位concept_id | 否定/时间 | 主体 | 数值 | 状态 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in result["rows"]:
        lines.append(f"| {r['span']} | {r['concept_name'] or r['zh']} | {r['concept_id'] or '—'} "
                     f"| {r['domain'] or '—'} | {r.get('site') or ''} | {r.get('site_concept_id') or ''} "
                     f"| {r['assertion']} | {r['subject']} | {r['value'] or ''} | **{r['status']}** |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render(run_case("患者发热。")))

# ============ 抽取缓存（用于双臂对照实验，保证两臂共享同一份抽取结果） ============
_CACHE_PATH = os.path.join(os.path.dirname(__file__), "extract_cache.json")
_CACHE = None


def _cache_load():
    global _CACHE
    if _CACHE is None:
        if os.path.exists(_CACHE_PATH):
            try:
                _CACHE = json.load(open(_CACHE_PATH, encoding="utf-8"))
            except Exception:
                _CACHE = {}
        else:
            _CACHE = {}
    return _CACHE


def extract_cached(text: str) -> list[dict]:
    """CACHE_EXTRACT=1 时按文本哈希缓存抽取结果；否则等价于 extract()。"""
    if os.environ.get("CACHE_EXTRACT") != "1":
        return extract(text)
    import hashlib
    key = hashlib.md5(text.encode("utf-8")).hexdigest()
    cache = _cache_load()
    if key in cache:
        return cache[key]
    res = extract(text)
    cache[key] = res
    json.dump(cache, open(_CACHE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    return res
