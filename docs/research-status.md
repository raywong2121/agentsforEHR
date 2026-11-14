# Research Status and Evidence Boundaries

This repository accompanies the manuscript
*面向 OHDSI/OMOP 的中文临床文本概念编码：一种六维完整编码的智能体方法*
(`docs/paper_zh.md`). Status as of the archived experiments.

## What the reported numbers do and do not establish

- **Do:** on the synthetic D5 set (100 notes, 1,013 facts), with the
  AI-adjudicated six-attribute gold, the full pipeline reaches 100.0%
  all-correct (95% CI lower bound 99.6%), and the structured pipeline vs.
  zero-shot gap is 100.0% vs. 0.0% all-correct (33.0% vs. 100.0% concept hit).
- **Do not:** clinical validity, generalization to real notes, or performance
  on a physician-adjudicated gold. The data are synthetic; the gold was built
  automatically and adjudicated by the authors (61% of disputed items were
  gold errors); the independent clinician spot-check is **pending**. All
  accuracy numbers are internal evidence (paper §6).

## Explicitly retracted claims

- **Strategy evolution improves performance.** An earlier online-evaluation
  claim was retracted after two implementation defects were found (the mined
  output was written to a file the pipeline never read, and the miner counted
  out-of-batch cases as misses) and after draft-strategy quality was measured
  (~40% problematic). The paper reports the mechanism and its boundaries only
  (paper §4.5).
- **Memory capacity curves from the leaky runs.** Batch-difference curves
  collected before the leakage fix are not reported as evidence.

## Known failure classes observed during development

All occurred in the pipeline's deterministic stages (ranking, acceptance,
abstention, field conventions) rather than in the model's semantic stage, and
each was fixed by a removable deterministic rule:

1. site merged into the concept (double counting);
2. questionnaire-vocabulary pollution of candidates (PhenX / CDC survey);
3. literal-similar but semantically different candidates ranking first
   (the CRP gene-variant trap);
4. over-strict self-check causing abstention (冠心病);
5. generic bare-domain concepts auto-accepted (`Measurement`);
6. site duplicated for concepts whose name contains the body part (胸痛→胸部).

See `prompts/PROMPTS.md` §5 for the corresponding rules and the paper's
Figure 3b for worked examples.

## Archive integrity

`data/` contains only synthetic cases, the gold standard, logged predictions
(including per-mention candidate lists), the frozen strategy library, and the
adjudication record. No credentials, patient data, or vocabulary database dump
is included; vocabulary-derived concept names appear only within evaluation
fixtures (see `NOTICE.md`).
