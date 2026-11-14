# Retrieval SQL (read-only, English standard path)

This is the parameterized query behind `retrieve()` in `src/retrieve.py`
(slightly simplified here; the code applies the same four match strengths and
deduplicates by taking the minimum `src`). `:term` is an English clinical term,
`:domain` an optional domain restriction (one of
Condition / Drug / Measurement / Observation / Procedure / Spec Anatomic Site).
Only `standard_concept = 'S'` rows are returned.

```sql
WITH cand AS (
  -- 1) exact standard concept name
  SELECT concept_id, 1 AS src FROM concept
   WHERE standard_concept = 'S'
     AND lower(concept_name) = lower(:term)
     AND (:domain IS NULL OR domain_id = :domain)
  UNION ALL
  -- 2) exact synonym
  SELECT cs.concept_id, 2
    FROM concept_synonym cs
    JOIN concept c ON c.concept_id = cs.concept_id AND c.standard_concept = 'S'
   WHERE lower(cs.concept_synonym_name) = lower(:term)
     AND (:domain IS NULL OR c.domain_id = :domain)
  UNION ALL
  -- 3) name contains the term
  SELECT concept_id, 3 FROM concept
   WHERE standard_concept = 'S'
     AND concept_name ILIKE '%' || :term || '%'
     AND (:domain IS NULL OR domain_id = :domain)
  UNION ALL
  -- 4) synonym contains the term
  SELECT cs.concept_id, 4
    FROM concept_synonym cs
    JOIN concept c ON c.concept_id = cs.concept_id AND c.standard_concept = 'S'
   WHERE cs.concept_synonym_name ILIKE '%' || :term || '%'
     AND (:domain IS NULL OR c.domain_id = :domain)
)
SELECT c.concept_id, c.concept_name, c.domain_id, c.vocabulary_id,
       min(x.src) AS src
  FROM cand x JOIN concept c USING (concept_id)
 GROUP BY 1, 2, 3, 4
 ORDER BY min(x.src), length(c.concept_name)
 LIMIT :limit;
```

Post-filtering in code: candidates whose names contain questionnaire markers
(`PhenX`, `[CDC`, `questionnaire`, `Survey`) are dropped, because they are
survey items rather than clinical findings.

The Chinese bridge path (`retrieve_cn()` in `src/retrieve.py`) matches the
Chinese span against `concept_synonym`, then follows the `Maps to` relationship
from non-standard vocabularies (ICD10CN is the only vocabulary with a
large-scale Chinese `Maps to` bridge in the deployed instance) to standard
concepts.

Ranking across the two paths: match strength first (exact name > exact synonym
> name containment > synonym containment), then the model's term order, then
domain agreement. English exact hits outrank Chinese-bridge hits.
