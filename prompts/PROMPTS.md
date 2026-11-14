# Prompt Roles and Deterministic Rules

The pipeline issues four kinds of single-turn model calls, all with JSON output
where structured, temperature 0. Prompts are kept in `src/pipeline.py`
(`EXTRACT_SYS`, `REFORMULATE_PROMPT`, `VERIFY_PROMPT`) and `src/strategy.py`
(`SELECT_PROMPT`). They are reproduced here for inspection; the code is authoritative.

## 1. Extraction (`EXTRACT_SYS`)

System prompt for the extraction step. The model must return, for each codable
clinical concept, a span, 1–3 English candidate terms, domain, assertion,
value, specimen, subject, and site. Key rules:

- Extract only symptoms / diseases / drugs / tests & measurements / anatomic sites.
- Site modifiers are separated into the `site` / `site_en` fields and never
  merged into the span (`双下肢水肿 → span 水肿, site 双下肢`).
- Core nouns only for spans; composite names stay whole (`糖化血红蛋白` is not split).
- Negation / status / time / degree / frequency words go into `assertion`;
  numeric values into `value`; verbs (`服用/停用/复查`) fold into `assertion`;
  the subject (`患者/胎儿/家属`) goes into `subject`.
- Blood pressure with two readings is split into two records:
  `span=收缩压 value=165` and `span=舒张压 value=110`; a single reading keeps
  `span=血压`.
- Lab items carry `specimen`: CBC (白细胞/血红蛋白) → 全血; biochemistry
  (肌酐/血糖/C反应蛋白) → 血清; urinalysis (尿酮体) → 尿.
- Performed exam procedures (心电图/CT/超声/胃镜) get `assertion=检查`.
- Concepts whose name already contains the site (胃溃疡/肺炎/胸痛/腹痛…) do not
  get a `site`; procedural composites are split (`头颅CT → span CT, site 头颅`).
- English terms prefer the most specific standard name
  (`高血压 → ["essential hypertension","hypertension"]`).

## 2. Reformulation (`REFORMULATE_PROMPT`)

Triggered when retrieval returns no strong candidate (and on failed
verification). The model receives the span, the Chinese concept, the intended
domain, and the terms already tried, and returns 2–3 alternative English terms.
Bounded to R ≤ 2 rounds per mention.

## 3. Selection (`SELECT_PROMPT`, in `src/strategy.py`)

Given the current span and the strategy menu, the model picks up to 4 applicable
strategy indices (JSON array). These are injected into the selection prompt.
Selection itself (`select()` in `pipeline.py`) is given the ranked candidate
list and must return one index, or −1 to abstain. The prompt biases the model
against abstention: it should pick the closest candidate whenever any
semantically related standard concept exists.

## 4. Verification (`VERIFY_PROMPT`)

Judges the selected concept against the source span and returns one word:
`match` / `narrow` / `broad` / `mismatch`. Any non-match verdict feeds one
correction cycle (reformulate → retrieve → select → reverify); a second
non-match results in explicit abstention (unmapped).

## 5. Deterministic checks (not model calls)

Rules applied inside `pipeline.py` after selection/verification. Each one was
introduced to fix a failure class observed during development, and each can be
removed independently:

| Rule | Condition | Action |
|---|---|---|
| Abstention veto | final result is `None` but the top candidate is a same-domain strong match (`src ≤ 2`) | accept the top candidate |
| Generic bare-domain rejection | final concept is a bare domain name (`Measurement`, `Procedure`, `Condition`, `Observation`, `Drug`, …) | replace with the same-domain candidate covering the most source term tokens |
| Gene-variant blacklist | candidate name contains `gene variant` / `genetic` / `allele` / `mutation` / … | excluded from replacement |
| Site convention | concept name already contains the body part | `site` must not duplicate it |
