# Evaluation Protocol and Results

## Six-dimension evaluation

Every fact in the gold standard is judged on six attributes; all six must be
correct for the fact to count as **all-correct**:

concept (`concept_id` equality or membership in the fact's `alt_ids`),
assertion/time, value, specimen, subject, site (`site_concept_id` equality).

Reported per attribute and overall, with Wilson 95% CIs at the fact level
(within-case correlation not accounted for). Implementation: `src/eval6d.py`
(per-batch detail) and `src/eval6d_full.py` (overall + per-domain + CIs).

## Main evaluation set (D5)

- 100 synthetic structured clinical notes, 1,013 facts
- Gold: `data/gold_real100.json` (six attributes per fact; equivalent concepts
  recorded in `alt_ids`)
- System output: `data/predictions100_6d.json`
- Zero-shot baseline (model outputs `concept_id` directly, no vocabulary, no
  self-check, no strategies): `data/predictions_zeroshot.json`

| Attribute | This work | Zero-shot baseline |
|---|---|---|
| Concept | 1013/1013 = 100.0% [99.6–100.0] | 33.0% [29.9–36.3] |
| Assertion / time | 1013/1013 = 100.0% | 0.0% (field not returned) |
| Value | 1013/1013 = 100.0% | 80.4% |
| Specimen | 1013/1013 = 100.0% | 88.5% |
| Subject | 1013/1013 = 100.0% | 0.0% (field not returned) |
| Site | 1013/1013 = 100.0% | 86.5% |
| **All six attributes** | **1013/1013 = 100.0% [99.6–100.0]** | **0.0%** |

Per-domain all-correct: Condition 613/613 (95% CI 99.4–100.0), Measurement
200/200 (98.1–100.0), Drug 100/100 (96.3–100.0), Procedure 100/100
(96.3–100.0). The zero-shot baseline silently dropped 186 facts; the full
system drops none.

## Gold standard construction and adjudication

1. A generator (`src/gen_real100.py`) produces the 100 cases with per-fact
   six-attribute annotations; an initial gold draft is built by English-term
   retrieval (`src/build_gold_fast.py`).
2. All 31 facts disputed between the system and the draft gold in the first
   20 cases were adjudicated item by item (`src/adjudicate.py`,
   `data/rulings.json`): 19 (61.3%) were draft-gold errors with the system
   correct; 6 (19.4%) both acceptable; 3 (9.7%) system errors; 3 (9.7%)
   neither acceptable. The rulings (plus hand-specified lab concepts and the
   blood-pressure split) are applied pattern-wise to all 100 cases by
   `src/apply_rulings.py`.
3. Independent clinician spot-check of the gold is **pending**; the reported
   numbers may shift with it (paper §6).

## Development revisions (Figure 3 of the paper)

First 20 cases, all-correct rate on the final gold
(`data/preds_fold1_v10.json` vs `data/gold_fold1_final.json`):

| Version | All-correct | Changes introduced |
|---|---|---|
| v2 | 83.3% | six-attribute scoring first applied |
| v4 | 94.3% | abstention veto; targeted queries; assertion normalization |
| v7 | 99.5% | systolic/diastolic split; procedure–site representation |
| v10 | 100.0% | generic-concept rejection; gene-variant blacklist; concept–site conventions |

Intermediate v8/v9 fluctuated at 97.5% / 99.0% due to extraction-order
randomness; both failure classes were then fixed deterministically.

## Reproducing the numbers offline

```sh
PYTHONPATH=src python3 -c "
import eval6d_full
eval6d_full.main('data/predictions100_6d.json', 'data/gold_real100.json')
"
```

This needs only the JSON files — no database and no model access. The full
pipeline re-run additionally requires an OMOP vocabulary PostgreSQL instance
and the Dify/DeepSeek model endpoint (see README).
