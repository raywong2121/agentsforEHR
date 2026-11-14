"""OHDSI 标准概念检索：英文临床术语 -> 候选 concept_id。

只读 SQL。词表：SNOMED(临床发现/程序)、RxNorm(药)、LOINC(检验/测量)。
排序：concept_name 精确 > concept_synonym 精确 > 名称包含 > 同义词包含；只取 standard_concept='S'。
"""
import os
import subprocess

DB = dict(
    host=os.environ.get("OHDSI_PGHOST", "127.0.0.1"),
    port=os.environ.get("OHDSI_PGPORT", "5432"),
    user=os.environ.get("OHDSI_PGUSER", "ohdsi_user"),
    db=os.environ.get("OHDSI_PGDB", "ohdsi"),
)

# 只在这些临床域里检索（排除 Device/Metadata/Unit 等非临床域）
CLINICAL_IN = ("('Condition','Drug','Measurement','Observation','Procedure','Spec Anatomic Site')")


def _q(sql: str) -> str:
    env = dict(os.environ, PGPASSWORD=os.environ.get("OHDSI_PASSWORD", ""),
               PGCLIENTENCODING="UTF8")
    if not env["PGPASSWORD"]:
        raise RuntimeError("缺少 OHDSI_PASSWORD 环境变量（词表只读密码）")
    r = subprocess.run(
        ["psql", "-h", DB["host"], "-p", str(DB["port"]), "-U", DB["user"],
         "-d", DB["db"], "-tA", "-F", "\t", "-c", sql],
        env=env, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:800])
    return r.stdout


def _esc(s: str) -> str:
    return s.replace("'", "''")


def retrieve(term: str, domain: str | None = None, limit: int = 10) -> list[dict]:
    """term 传英文临床术语；domain 传 Condition/Drug/Measurement/Observation/Procedure。"""
    t = _esc(term.strip())
    d = f"domain_id = '{_esc(domain)}'" if domain else f"domain_id IN {CLINICAL_IN}"

    sql = f"""
    WITH cand AS (
      SELECT concept_id, 1 AS src, 1.0 AS sim FROM concept
       WHERE standard_concept='S' AND lower(concept_name) = lower('{t}') AND {d}
      UNION ALL
      SELECT cs.concept_id, 2, 1.0 FROM concept_synonym cs
       JOIN concept c ON c.concept_id = cs.concept_id AND c.standard_concept='S'
       WHERE lower(cs.concept_synonym_name) = lower('{t}') AND {d.replace('domain_id', 'c.domain_id')}
      UNION ALL
      SELECT concept_id, 3, 0.6 FROM concept
       WHERE standard_concept='S' AND concept_name ILIKE '%{t}%' AND {d}
      UNION ALL
      SELECT cs.concept_id, 4, 0.4 FROM concept_synonym cs
       JOIN concept c ON c.concept_id = cs.concept_id AND c.standard_concept='S'
       WHERE cs.concept_synonym_name ILIKE '%{t}%' AND {d.replace('domain_id', 'c.domain_id')}
    )
    SELECT c.concept_id, c.concept_name, c.domain_id, c.vocabulary_id,
           min(x.src) AS src, round(max(x.sim)::numeric, 3) AS sim
    FROM cand x JOIN concept c USING (concept_id)
    GROUP BY 1,2,3,4
    ORDER BY min(x.src), max(x.sim) DESC, length(c.concept_name)
    LIMIT {limit};
    """
    out = []
    for line in _q(sql).splitlines():
        if not line.strip():
            continue
        p = line.split("\t")
        if len(p) >= 6:
            out.append({"concept_id": int(p[0]), "concept_name": p[1], "domain": p[2],
                        "vocabulary": p[3], "src": int(p[4]), "sim": p[5]})
    # 过滤问卷/量表类概念（PhenX、CDC 问卷等），它们不是临床发现
    BAD = ("PhenX", "[CDC", "questionnaire", "Survey", "survey")
    return [c for c in out if not any(b in c["concept_name"] for b in BAD)]


def retrieve_cn(zh_term: str, domain: str | None = None, limit: int = 10) -> list[dict]:
    """中文直达标准概念：中文同义词 -> 非标准概念(ICD10CN等) -> 'Maps to' -> 标准概念。

    直接命中标准概念的名称/同义词也一并返回。
    """
    t = _esc(zh_term.strip())
    d = f"c.domain_id = '{_esc(domain)}'" if domain else f"c.domain_id IN {CLINICAL_IN}"
    sql = f"""
    WITH src AS (
      -- 中文同义词命中的非标准概念
      SELECT cs.concept_id AS mid FROM concept_synonym cs
       WHERE cs.concept_synonym_name = '{t}'
      UNION
      -- 标准概念自身中文同义词
      SELECT cs.concept_id AS mid FROM concept_synonym cs
       JOIN concept c ON c.concept_id = cs.concept_id AND c.standard_concept='S'
       WHERE cs.concept_synonym_name = '{t}'
    ),
    targets AS (
      SELECT m.concept_id_2 AS cid, 1 AS src FROM src s
       JOIN concept_relationship m ON m.concept_id_1 = s.mid AND m.relationship_id = 'Maps to'
      UNION ALL
      SELECT s.mid, 2 FROM src s JOIN concept c ON c.concept_id = s.mid AND c.standard_concept='S'
    )
    SELECT c.concept_id, c.concept_name, c.domain_id, c.vocabulary_id,
           min(t.src) AS src, '' AS sim
    FROM targets t JOIN concept c ON c.concept_id = t.cid
    WHERE c.standard_concept='S' AND {d}
    GROUP BY 1,2,3,4
    ORDER BY min(t.src), length(c.concept_name)
    LIMIT {limit};
    """
    out = []
    for line in _q(sql).splitlines():
        if not line.strip():
            continue
        p = line.split("\t")
        if len(p) >= 5:
            out.append({"concept_id": int(p[0]), "concept_name": p[1], "domain": p[2],
                        "vocabulary": p[3], "src": int(p[4]), "sim": ""})
    return out


if __name__ == "__main__":
    tests = [
        ("fever", "Condition"),
        ("essential hypertension", "Condition"),
        ("insomnia", "Condition"),
        ("metformin", "Drug"),
        ("chest tightness", "Condition"),
        ("pneumonia", "Condition"),
        ("atrial fibrillation", "Condition"),
        ("pre-eclampsia", "Condition"),
        ("asthma", "Condition"),
        ("type 2 diabetes mellitus", "Condition"),
        ("tinnitus", "Condition"),
        ("nausea", "Condition"),
    ]
    for term, dom in tests:
        print("=" * 64)
        print(f"TERM: {term}  [{dom}]")
        for c in retrieve(term, dom, limit=6):
            print(f"  {c['concept_id']:>9}  {c['domain']:<12} {c['vocabulary']:<8} s{c['src']} sim={c['sim']:<5} {c['concept_name']}")
