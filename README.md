# Six-Dimension Complete Coding Agent for Chinese Clinical Free Text → OMOP Standard Concepts

Reference implementation of the coding pipeline described in the companion
manuscript: each clinical fact in Chinese free text is coded as a
**six-attribute record** — concept, assertion/time, value, specimen, subject,
and anatomic site — rather than a bare `concept_id`. Standard OMOP concepts
carry English names only, so the pipeline performs the Chinese→English semantic
bridge itself: model extraction with candidate English terms, dual-path
vocabulary retrieval (ICD10CN "Maps to" bridge + English name/synonym search),
bounded reformulation, selection or explicit abstention, single-model
self-verification with one correction cycle, and deterministic checks — rather
than a hand-built term dictionary.

This repository contains **source code only**. Evaluation data, results, and
figures are not distributed here; contact the authors or see the manuscript.

## Pipeline

1. **Extraction** — one model call returns spans with 1–3 English candidate
   terms plus the six attributes (assertion, value, specimen, subject, site),
   following the rules in [`prompts/PROMPTS.md`](prompts/PROMPTS.md).
2. **Dual-path retrieval** — English terms query the standard vocabulary
   ([`sql/retrieval.sql`](sql/retrieval.sql)); the Chinese span queries the
   ICD10CN `Maps to` bridge. Candidates ranked by match strength → term order
   → domain.
3. **Reformulation loop** — no strong candidate ⇒ the model reformulates the
   English terms (≤ 2 rounds).
4. **Selection / abstention** — a strong same-domain top hit with no
   applicable strategy is accepted deterministically; otherwise the model
   selects among candidates or outputs an explicit abstention.
5. **Conditional verification** — skipped only for exact-name hits with no
   applicable strategy; verdicts `match / narrow / broad / mismatch`.
6. **One correction cycle** — a non-match verdict triggers
   reformulate → retrieve → select → reverify; a second non-match is an
   explicit `unmapped`, never a silent drop.
7. **Deterministic checks** — abstention veto, generic bare-domain rejection,
   gene-variant blacklist, concept–site conventions
   ([`prompts/PROMPTS.md`](prompts/PROMPTS.md) §5).
8. **Coverage check** — every extracted fact must carry a `concept_id` or an
   explicit `unmapped` status.

## Repository map

| Component | Path |
|---|---|
| Main pipeline | [`src/pipeline.py`](src/pipeline.py) |
| Dual-path retrieval | [`src/retrieve.py`](src/retrieve.py), [`sql/retrieval.sql`](sql/retrieval.sql) |
| Strategy-level memory (trigger · action · example) | [`src/strategy.py`](src/strategy.py) |
| Six-dimension evaluation (detail / overall with Wilson CIs) | [`src/eval6d.py`](src/eval6d.py), [`src/eval6d_full.py`](src/eval6d_full.py) |
| Batch runner with per-case watchdog | [`src/run_cases.py`](src/run_cases.py) |
| Synthetic case generator / gold builder / rulings application | [`src/gen_real100.py`](src/gen_real100.py), [`src/build_gold_fast.py`](src/build_gold_fast.py), [`src/apply_rulings.py`](src/apply_rulings.py) |
| Strategy-evolution experiments (bounded; see manuscript) | [`src/mine.py`](src/mine.py), [`src/mine_modes.py`](src/mine_modes.py), [`src/approve.py`](src/approve.py), [`src/two_arm.py`](src/two_arm.py) |
| Prompt roles and deterministic rules | [`prompts/PROMPTS.md`](prompts/PROMPTS.md) |
| Offline contract tests (no services required) | [`tests/`](tests/) |

## Offline checks

The offline tests use only the Python standard library; they import the
modules, verify helper semantics, and check that the documented deterministic
rules are present. They make no model calls and need no database.

```sh
python3 -m unittest discover -s tests -v
```

## Running the full pipeline (requires services)

1. **Vocabulary database** — PostgreSQL with the OMOP vocabulary tables
   (`concept`, `concept_synonym`, `concept_relationship`, `domain`,
   `vocabulary`) and a read-only user. Set
   `OHDSI_PGHOST / OHDSI_PGPORT / OHDSI_PGUSER / OHDSI_PGDB / OHDSI_PASSWORD`;
   the `psql` client binary must be on PATH.
2. **Model endpoint** — either a DeepSeek-compatible API
   (`DEEPSEEK_API_KEY`, optional `DEEPSEEK_MODEL`), or the Dify console path
   ([`src/flash.py`](src/flash.py)): `DIFY_BASE_URL`, `DIFY_APP_ID`,
   `DIFY_MANAGEMENT_MODULE` (path to a local Dify console source package
   providing `management.Api`), `DIFY_EMAIL`, `DIFY_PASSWORD`. The archived
   runs used `deepseek-v4-flash` at temperature 0 via the Dify console.

```sh
PYTHONPATH=src python3 gen_real100.py                    # -> cases_real100.json
PYTHONPATH=src python3 build_gold_fast.py cases_real100.json gold_real100.json
PYTHONPATH=src python3 apply_rulings.py
PYTHONPATH=src python3 run_cases.py cases_real100.json predictions.json
PYTHONPATH=src python3 eval6d_full.py predictions.json gold_real100.json
```

`run_cases.py` caches extractions by text hash (`CACHE_EXTRACT=0` to disable)
and enforces a 300-s per-case watchdog. Model output is non-deterministic even
at temperature 0; run-to-run variation was observed during development.

## Evidence boundaries

- The manuscript's evaluation set is synthetic; real-note performance is not
  claimed.
- The gold was built automatically and adjudicated by the authors; independent
  clinician spot-check is pending.
- The earlier claim that online strategy growth improves performance was
  retracted; the evolution layer is reported with its boundaries only.
- No credentials, patient data, vocabulary dumps, or evaluation artifacts are
  included. See [`NOTICE.md`](NOTICE.md) for third-party licensing.
