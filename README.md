# Six-Dimension Complete Coding Agent for Chinese Clinical Free Text → OMOP Standard Concepts

Code and data accompanying the manuscript

> **面向 OHDSI/OMOP 的中文临床文本概念编码：一种六维完整编码的智能体方法**
> (*A Six-Dimension Complete Coding Agent for Chinese Free Text to OMOP Standard Concepts*)

Full paper (Chinese): [`docs/paper_zh.md`](docs/paper_zh.md). Figures:
[`figures/`](figures/) (SVG sources and 300-dpi PNG renders).

The system codes each clinical fact in Chinese free text as a **six-attribute
record** — concept, assertion/time, value, specimen, subject, and anatomic
site — instead of a bare `concept_id`. Standard OMOP concepts carry English
names only, so the system performs the Chinese→English semantic bridge itself:
model extraction with candidate English terms, dual-path vocabulary retrieval
(ICD10CN "Maps to" bridge + English name/synonym search), bounded
reformulation, selection or explicit abstention, single-model self-verification
with one correction cycle, and a set of **deterministic checks** — rather than
a hand-built term dictionary.

## Reported results (synthetic evaluation set, 100 cases / 1,013 facts)

| Attribute | This work | Zero-shot LLM baseline |
|---|---|---|
| Concept | 1,013/1,013 — 100.0% [99.6–100.0] | 33.0% |
| Assertion / time | 100.0% | 0.0% (field not returned) |
| Value | 100.0% | 80.4% |
| Specimen | 100.0% | 88.5% |
| Subject | 100.0% | 0.0% (field not returned) |
| Anatomic site | 100.0% | 86.5% |
| **All six attributes** | **100.0% [99.6–100.0]** | **0.0%** |

Per-domain all-correct: Condition 613/613, Measurement 200/200, Drug 100/100,
Procedure 100/100. See [`docs/evaluation.md`](docs/evaluation.md) and
[`docs/research-status.md`](docs/research-status.md) for what these numbers do
and do not establish (synthetic data; AI-adjudicated gold; independent
clinician spot-check pending).

## Repository map

| Component | Path |
|---|---|
| Pipeline (extract → retrieve → reformulate → select/abstain → verify → deterministic checks → six-attribute output) | [`src/pipeline.py`](src/pipeline.py) |
| Dual-path retrieval + read-only SQL | [`src/retrieve.py`](src/retrieve.py), [`sql/retrieval.sql`](sql/retrieval.sql) |
| Strategy-level memory (trigger · action · example) | [`src/strategy.py`](src/strategy.py) |
| Six-dimension evaluation (detail / overall with Wilson CIs) | [`src/eval6d.py`](src/eval6d.py), [`src/eval6d_full.py`](src/eval6d_full.py) |
| Batch runner with per-case watchdog | [`src/run_cases.py`](src/run_cases.py) |
| Case generator, gold builder, rulings application | [`src/gen_real100.py`](src/gen_real100.py), [`src/build_gold_fast.py`](src/build_gold_fast.py), [`src/apply_rulings.py`](src/apply_rulings.py) |
| Adjudication assistant + 31 rulings | [`src/adjudicate.py`](src/adjudicate.py), [`data/rulings.json`](data/rulings.json) |
| Strategy-evolution experiments (bounded; see paper §4.5) | [`src/mine.py`](src/mine.py), [`src/mine_modes.py`](src/mine_modes.py), [`src/approve.py`](src/approve.py), [`src/two_arm.py`](src/two_arm.py) |
| Prompt roles and deterministic rules | [`prompts/PROMPTS.md`](prompts/PROMPTS.md) |
| Evaluation data (gold, predictions, strategy library) | [`data/`](data/) |
| Architecture / evaluation / evidence boundaries | [`docs/`](docs/) |

## Reproduce the reported numbers (offline, no dependencies)

The shipped JSON files allow re-computing every table in the paper with the
Python standard library only:

```sh
python3 -m unittest discover -s tests -v          # 12 offline contract checks

PYTHONPATH=src python3 -c "
import eval6d_full
eval6d_full.main('data/predictions100_6d.json', 'data/gold_real100.json')
"
```

## Run the full pipeline (requires services)

1. **Vocabulary database** — a PostgreSQL instance with the OMOP vocabulary
   tables (`concept`, `concept_synonym`, `concept_relationship`, `domain`,
   `vocabulary`) and a read-only user. Configure
   `OHDSI_PGHOST / OHDSI_PGPORT / OHDSI_PGUSER / OHDSI_PGDB / OHDSI_PASSWORD`.
   The pipeline also uses the `psql` client binary.
2. **Model endpoint** — either a direct DeepSeek-compatible API
   (`DEEPSEEK_API_KEY`, optional `DEEPSEEK_MODEL`), or the Dify console path
   (`src/flash.py`): `DIFY_BASE_URL`, `DIFY_APP_ID`, `DIFY_MANAGEMENT_MODULE`
   (path to the local Dify console source package providing `management.Api`),
   `DIFY_EMAIL`, `DIFY_PASSWORD`. The archived runs used
   `deepseek-v4-flash` at temperature 0 via the Dify console.
3. Generate cases and gold, then run:

```sh
PYTHONPATH=src python3 gen_real100.py                    # -> cases_real100.json
PYTHONPATH=src python3 build_gold_fast.py cases_real100.json gold_real100.json
PYTHONPATH=src python3 apply_rulings.py                  # applies adjudicated rulings
PYTHONPATH=src python3 run_cases.py cases_real100.json predictions.json
PYTHONPATH=src python3 eval6d_full.py predictions.json gold_real100.json
```

`run_cases.py` caches extractions by text hash (set `CACHE_EXTRACT=0` to
disable) and gives each case a 300-s watchdog. Model output is
non-deterministic even at temperature 0; the archived runs share an extraction
cache, and run-to-run variation of roughly one point (and up to ~10 points
without a shared cache) was observed during development — see the paper's
limitations.

## Evidence boundaries (see [`docs/research-status.md`](docs/research-status.md))

- The evaluation set is **synthetic**; real-note performance is not claimed.
- The gold was built automatically and **adjudicated by the authors**
  (61% of disputed items were gold errors); independent clinician spot-check
  is **pending**.
- The earlier claim that online strategy growth improves performance was
  **retracted**; the evolution layer is reported with its boundaries only.
- No credentials, patient data, or vocabulary database dump are included.
  See [`NOTICE.md`](NOTICE.md) for licensing of third-party materials.
