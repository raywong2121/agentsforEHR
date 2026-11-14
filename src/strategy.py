"""策略级记忆：存"可迁移的编码策略"，不存单条术语映射。

每条策略 = 触发条件 + 动作 + 示例。检索时按当前片段判断哪几条策略适用。
"""
import json
import os

import pipeline

STRAT_PATH = os.path.join(os.path.dirname(__file__), "experience_strategies.jsonl")

SELECT_PROMPT = (
    "下面是临床概念编码的可迁移策略。当前片段「{span}」（中文概念：{zh}，域：{domain}）。\n"
    "请挑出与这个片段相关、应当遵循的策略序号（最多 4 个），只输出 JSON 数组如 [0,2]；都不相关输出 []。\n\n"
    "策略列表：\n{menu}"
)


def load():
    if not os.path.exists(STRAT_PATH):
        return []
    out = []
    with open(STRAT_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def relevant(span: str, zh: str, domain: str | None = None, max_n: int = 4) -> list[dict]:
    strs = load()
    if not strs:
        return []
    menu = "\n".join(f"{i}: [{s.get('name','')}] 触发：{s.get('trigger','')}；动作：{s.get('action','')}"
                     for i, s in enumerate(strs))
    try:
        raw = pipeline.llm([{"role": "user", "content": SELECT_PROMPT.format(
            span=span, zh=zh, domain=domain or "临床", menu=menu[:3500])}], max_tokens=40, json_mode=True)
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("data", [])
        idxs = [i for i in data if isinstance(i, int)]
    except Exception:
        return []
    return [strs[i] for i in idxs[:max_n] if 0 <= i < len(strs)]


def as_prompt_block(strs: list[dict]) -> str:
    if not strs:
        return ""
    lines = "\n".join(f"- [{s.get('name','')}] {s.get('action','')}（示例：{'; '.join(s.get('examples', [])[:2])}）"
                      for s in strs)
    return "\n【编码策略（必须遵循）】\n" + lines + "\n"


def targets(span: str, zh: str, domain: str | None = None) -> list[str]:
    """策略里若点名了具体目标术语，提取出来用于反推检索。
    先做确定性匹配（span/zh 出现在策略名或示例里），再用模型相关性做补充。"""
    out = []
    hay = (span or "") + "|" + (zh or "")
    for s in load():
        hit = False
        if hay:
            if s.get("name") and s["name"] in hay:
                hit = True
            for e in s.get("examples", []) or []:
                if hay.split("|")[0] and hay.split("|")[0] in str(e):
                    hit = True
        if hit:
            for t in s.get("targets", []) or []:
                if t not in out:
                    out.append(t)
    for s in relevant(span, zh, domain):
        for t in s.get("targets", []) or []:
            if t not in out:
                out.append(t)
    return out
