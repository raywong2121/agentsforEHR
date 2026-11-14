# System Architecture

The system codes Chinese clinical free text into OMOP standard concepts as a
**six-attribute record** per clinical fact:

| Attribute | Meaning |
|---|---|
| concept | standard `concept_id` (or explicit unmapped) |
| assertion / time | 肯定 / 否定 / 疑似 / 既往 / 服用中 / 检查 … |
| value | numeric value (blood pressure split into systolic + diastolic records) |
| specimen | 全血 / 血清 / 血浆 / 尿 / null |
| subject | 患者 / 胎儿 / 患者家属 |
| site | separate anatomic-site standard concept |

The pipeline (Figure 1 of the paper) has three layers.

## Execution layer

1. **Extraction** — one model call returns spans with English candidate terms
   and the attribute fields above, following the rules in `prompts/PROMPTS.md`.
2. **Dual-path retrieval** — English terms query the standard vocabulary
   (`sql/retrieval.sql`); the Chinese span queries the ICD10CN `Maps to` bridge.
   Candidates are ranked by match strength → term order → domain.
3. **Reformulation loop** — if no candidate reaches the strong-match threshold,
   the model reformulates the English terms (≤ 2 rounds).
4. **Selection / abstention** — a strong same-domain top hit with no applicable
   strategy is accepted deterministically; otherwise the model selects among
   candidates or outputs an explicit abstention.

## Reflection layer

5. **Conditional verification** — skipped only for exact-name hits with no
   applicable strategy. The model judges the selected concept against the span
   (`match` / `narrow` / `broad` / `mismatch`).
6. **One correction cycle** — a non-match verdict triggers
   reformulate → retrieve → select → reverify; a second non-match is an
   explicit abstention (`unmapped`), never a silent drop.

## Deterministic checks

Applied after selection/verification (see `prompts/PROMPTS.md` §5): abstention
veto, generic bare-domain rejection, gene-variant blacklist, and
concept–site conventions. Each was added for one observed failure class and
each can be removed independently, which is how the paper's development table
(Figure 3a) attributes its gains.

## Evolution layer (explored, bounded)

Failures are distilled into strategies of the form *trigger · action · example*
(`data/strategies_frozen10.jsonl`), injected into later selections. Two gates:
a consistency check against existing strategies (direction conflicts rejected)
and manual approval. The paper reports this mechanism's boundaries: roughly
40% of automatically mined drafts were problematic because mining inherits the
reference standard's bias; no performance gain from online strategy growth is
claimed (paper §4.5).

## Coverage check

The final output is checked against the extracted spans: every extracted fact
must either carry a `concept_id` or an explicit `unmapped` status. No silent
drops.
