WITH safety AS MATERIALIZED (
  SELECT pg_catalog.set_config('transaction_read_only', 'on', true) AS ro,
         pg_catalog.current_setting('statement_timeout') AS statement_timeout,
         pg_catalog.current_setting('lock_timeout') AS lock_timeout
), input_payload AS MATERIALIZED (
  SELECT CAST($arg0 AS JSONB) AS payload FROM safety WHERE safety.ro = 'on'
), request AS MATERIALIZED (
  SELECT payload,
    CASE WHEN pg_catalog.jsonb_typeof(payload)='array' THEN 'search'
         WHEN pg_catalog.jsonb_typeof(payload)='object' THEN pg_catalog.jsonb_extract_path_text(payload,'mode') ELSE NULL END AS mode,
    pg_catalog.jsonb_typeof(payload)='array' AS legacy_array,
    CASE WHEN pg_catalog.jsonb_typeof(payload)='array' THEN payload
         WHEN pg_catalog.jsonb_typeof(pg_catalog.jsonb_extract_path(payload,'terms'))='array' THEN pg_catalog.jsonb_extract_path(payload,'terms') ELSE '[]'::JSONB END AS term_values,
    CASE WHEN pg_catalog.jsonb_typeof(pg_catalog.jsonb_extract_path(payload,'concept_ids'))='array' THEN pg_catalog.jsonb_extract_path(payload,'concept_ids') ELSE '[]'::JSONB END AS id_values
  FROM input_payload
), raw_terms AS MATERIALIZED (
  SELECT pg_catalog.jsonb_extract_path_text(pg_catalog.jsonb_build_object('v',item.value),'v') AS term, item.term_index::INTEGER AS term_index,
         pg_catalog.jsonb_typeof(item.value)='string' AS is_string
  FROM request CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(request.term_values)
    WITH ORDINALITY AS item(value,term_index) WHERE request.mode='search'
), raw_ids AS MATERIALIZED (
  SELECT CASE WHEN pg_catalog.jsonb_typeof(item.value)='number'
                    AND pg_catalog.regexp_match(pg_catalog.jsonb_extract_path_text(pg_catalog.jsonb_build_object('v',item.value),'v'),'^[1-9][0-9]{0,9}$') IS NOT NULL
              THEN CASE WHEN (pg_catalog.jsonb_extract_path_text(pg_catalog.jsonb_build_object('v',item.value),'v'))::NUMERIC <= 2147483647
                        THEN (pg_catalog.jsonb_extract_path_text(pg_catalog.jsonb_build_object('v',item.value),'v'))::INTEGER ELSE NULL END
              ELSE NULL END AS concept_id,
         item.id_index::INTEGER AS id_index
  FROM request CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(request.id_values)
    WITH ORDINALITY AS item(value,id_index) WHERE request.mode='meaning'
), input_check AS MATERIALIZED (
  SELECT CASE WHEN request.mode='search' THEN
    (request.legacy_array OR (pg_catalog.jsonb_typeof(request.payload)='object'
       AND pg_catalog.jsonb_typeof(pg_catalog.jsonb_extract_path(request.payload,'terms'))='array'
       AND (request.payload-'mode'-'terms')='{}'::JSONB))
    AND (SELECT pg_catalog.count(*) BETWEEN 1 AND CASE WHEN request.legacy_array THEN 8 ELSE 6 END
      AND COALESCE(pg_catalog.bool_and(is_string AND term IS NOT NULL
          AND pg_catalog.length(pg_catalog.btrim(term))>0 AND pg_catalog.length(term)<=100),FALSE) FROM raw_terms)
    WHEN request.mode='meaning' THEN
      pg_catalog.jsonb_typeof(request.payload)='object'
      AND pg_catalog.jsonb_typeof(pg_catalog.jsonb_extract_path(request.payload,'concept_ids'))='array'
      AND (request.payload-'mode'-'concept_ids')='{}'::JSONB
      AND (SELECT pg_catalog.count(*) BETWEEN 1 AND 6
           AND pg_catalog.count(DISTINCT concept_id)=pg_catalog.count(*) FROM raw_ids)
    ELSE FALSE END AS valid
  FROM request
), terms AS MATERIALIZED (
  SELECT raw_terms.term,raw_terms.term_index FROM raw_terms CROSS JOIN input_check CROSS JOIN safety
  WHERE input_check.valid AND safety.ro='on'
), exact_raw AS MATERIALIZED (
  SELECT t.term_index, t.term, cs.concept_id,
         cs.concept_synonym_name AS matched_text,
         cs.language_concept_id AS matched_language_concept_id,
         'concept_synonym_name'::TEXT AS matched_field,
         'exact_case_insensitive'::TEXT AS match_mode,
         CASE WHEN cs.language_concept_id = 4182948 THEN 0 ELSE 1 END AS priority
  FROM terms AS t
  JOIN public.concept_synonym AS cs ON pg_catalog.lower(cs.concept_synonym_name) = pg_catalog.lower(t.term)
  UNION ALL
  SELECT t.term_index, t.term, c.concept_id, c.concept_name,
         NULL::INTEGER, 'concept_name'::TEXT, 'exact_case_insensitive'::TEXT, 2
  FROM terms AS t
  JOIN public.concept AS c ON pg_catalog.lower(c.concept_name) = pg_catalog.lower(t.term)
), exact_hits AS MATERIALIZED (
  SELECT DISTINCT ON (term_index, concept_id) * FROM exact_raw
  ORDER BY term_index, concept_id, priority, matched_text,
           matched_language_concept_id NULLS LAST
), unmatched_terms AS MATERIALIZED (
  SELECT t.* FROM terms AS t
  WHERE NOT EXISTS (SELECT 1 FROM exact_hits AS e WHERE e.term_index = t.term_index)
), phrase_terms AS MATERIALIZED (
  SELECT t.* FROM unmatched_terms AS t CROSS JOIN safety
  WHERE pg_catalog.length(t.term) >= 2
    AND safety.statement_timeout IN
      ('1ms','5ms','10ms','20ms','50ms','100ms','200ms','500ms',
       '1s','2s','3s','4s','5s')
), phrase_raw AS MATERIALIZED (
  SELECT t.term_index, t.term, cs.concept_id,
         cs.concept_synonym_name AS matched_text,
         cs.language_concept_id AS matched_language_concept_id,
         'concept_synonym_name'::TEXT AS matched_field,
         'literal_case_insensitive_substring'::TEXT AS match_mode,
         CASE WHEN cs.language_concept_id = 4182948 THEN 10 ELSE 11 END AS priority
  FROM phrase_terms AS t
  JOIN public.concept_synonym AS cs
    ON pg_catalog.strpos(pg_catalog.lower(cs.concept_synonym_name), pg_catalog.lower(t.term)) > 0
  UNION ALL
  SELECT t.term_index, t.term, c.concept_id, c.concept_name,
         NULL::INTEGER, 'concept_name'::TEXT,
         'literal_case_insensitive_substring'::TEXT, 12
  FROM phrase_terms AS t
  JOIN public.concept AS c ON pg_catalog.strpos(pg_catalog.lower(c.concept_name), pg_catalog.lower(t.term)) > 0
), phrase_hits AS MATERIALIZED (
  SELECT DISTINCT ON (term_index, concept_id) * FROM phrase_raw
  ORDER BY term_index, concept_id, priority, matched_text,
           matched_language_concept_id NULLS LAST
), all_hits AS MATERIALIZED (
  SELECT * FROM exact_hits UNION ALL SELECT * FROM phrase_hits
), ranked_hits AS MATERIALIZED (
  SELECT all_hits.*,
         pg_catalog.row_number() OVER (PARTITION BY term_index ORDER BY priority, concept_id,
                            matched_text, matched_language_concept_id NULLS LAST) AS rank
  FROM all_hits
), kept_hits AS MATERIALIZED (
  SELECT * FROM ranked_hits WHERE rank <= 50
), source_ids AS MATERIALIZED (
  SELECT DISTINCT concept_id FROM kept_hits
), missing_source_identities AS MATERIALIZED (
  SELECT s.concept_id, 'concept_row_missing'::TEXT AS reason
  FROM source_ids AS s
  LEFT JOIN public.concept AS c ON c.concept_id = s.concept_id
  WHERE c.concept_id IS NULL
), map_probe AS MATERIALIZED (
  SELECT r.concept_id_1 AS source_id, r.concept_id_2 AS target_id,
         r.relationship_id, r.valid_start_date, r.valid_end_date, r.invalid_reason
  FROM source_ids AS s
  JOIN public.concept AS source ON source.concept_id = s.concept_id
  JOIN public.concept_relationship AS r ON r.concept_id_1 = s.concept_id
  JOIN public.concept AS target ON target.concept_id = r.concept_id_2
  CROSS JOIN safety
  WHERE safety.ro = 'on'
    AND r.relationship_id IN ('Maps to', 'Maps to value')
    AND r.invalid_reason IS NULL
    AND CURRENT_DATE BETWEEN r.valid_start_date AND r.valid_end_date
    AND target.standard_concept = 'S' AND target.invalid_reason IS NULL
    AND CURRENT_DATE BETWEEN target.valid_start_date AND target.valid_end_date
  ORDER BY r.concept_id_1, r.relationship_id, r.concept_id_2,
           r.valid_start_date, r.valid_end_date
  LIMIT 401
), maps AS MATERIALIZED (
  SELECT * FROM map_probe
  ORDER BY source_id, relationship_id, target_id, valid_start_date, valid_end_date
  LIMIT 400
), concept_ids AS MATERIALIZED (
  SELECT concept_id FROM source_ids UNION SELECT target_id FROM maps
), concepts AS MATERIALIZED (
  SELECT c.concept_id, c.concept_name, c.vocabulary_id, c.concept_code,
         c.concept_class_id, c.domain_id, c.standard_concept,
         c.valid_start_date, c.valid_end_date, c.invalid_reason
  FROM concept_ids AS ids
  JOIN public.concept AS c ON c.concept_id = ids.concept_id
), term_results AS MATERIALIZED (
  SELECT t.term_index, t.term, 50 AS cap,
         (SELECT pg_catalog.count(*)::INTEGER FROM kept_hits k
          WHERE k.term_index = t.term_index) AS returned_count,
         EXISTS (SELECT 1 FROM ranked_hits h
                 WHERE h.term_index = t.term_index AND h.rank = 51) AS truncated,
         CASE
           WHEN EXISTS (SELECT 1 FROM exact_hits e WHERE e.term_index = t.term_index)
             THEN 'not_needed_exact_hit'
           WHEN pg_catalog.length(t.term) < 2 THEN 'skipped_short_term'
           WHEN EXISTS (SELECT 1 FROM phrase_terms p WHERE p.term_index = t.term_index)
             THEN 'attempted'
           ELSE 'skipped_preexisting_timeout_not_bounded'
         END AS phrase_stage
  FROM terms AS t
), versions AS MATERIALIZED (
  SELECT v.vocabulary_id, v.vocabulary_name, v.vocabulary_version,
         v.vocabulary_concept_id
  FROM public.vocabulary AS v
  WHERE v.vocabulary_id IN (SELECT DISTINCT vocabulary_id FROM concepts)
), meaning_auto_sources AS MATERIALIZED (
  SELECT k.concept_id,k.term_index
  FROM kept_hits k WHERE k.match_mode='exact_case_insensitive'
  UNION ALL
  SELECT m.target_id AS concept_id,k.term_index
  FROM kept_hits k JOIN maps m ON m.source_id=k.concept_id
  WHERE k.match_mode='exact_case_insensitive' AND m.relationship_id='Maps to'
), meaning_auto AS MATERIALIZED (
  SELECT a.concept_id,pg_catalog.min(a.term_index) AS min_term_index
  FROM meaning_auto_sources a JOIN public.concept c ON c.concept_id=a.concept_id
  JOIN public.vocabulary v ON v.vocabulary_id=c.vocabulary_id
  WHERE c.standard_concept='S'
    AND c.invalid_reason IS NULL AND CURRENT_DATE BETWEEN c.valid_start_date AND c.valid_end_date
    AND c.concept_code IS NOT NULL AND pg_catalog.btrim(c.concept_code)<>''
    AND v.vocabulary_version IS NOT NULL AND pg_catalog.btrim(v.vocabulary_version)<>''
  GROUP BY a.concept_id
), meaning_auto_rank AS MATERIALIZED (
  SELECT *,pg_catalog.row_number() OVER (ORDER BY min_term_index,concept_id) AS selection_rank FROM meaning_auto
), meaning_selected AS MATERIALIZED (
  SELECT a.concept_id,a.selection_rank::INTEGER AS selection_index
  FROM meaning_auto_rank a CROSS JOIN request CROSS JOIN input_check
  WHERE request.mode='search' AND input_check.valid AND a.selection_rank<=6
  UNION ALL
  SELECT i.concept_id,i.id_index FROM raw_ids i CROSS JOIN request CROSS JOIN input_check
  WHERE request.mode='meaning' AND input_check.valid
), meaning_sources AS MATERIALIZED (
  SELECT ms.concept_id AS requested_id,ms.selection_index,mc.concept_id IS NOT NULL AS found,
    CASE WHEN mc.concept_id IS NULL THEN NULL ELSE pg_catalog.jsonb_build_object('concept_id', mc.concept_id, 'concept_name', mc.concept_name, 'vocabulary_id', mc.vocabulary_id, 'concept_code', mc.concept_code, 'concept_class_id', mc.concept_class_id, 'domain_id', mc.domain_id, 'standard_concept', mc.standard_concept, 'valid_start_date', mc.valid_start_date, 'valid_end_date', mc.valid_end_date, 'invalid_reason', mc.invalid_reason, 'vocabulary_version', mv.vocabulary_version) END AS source_identity,
    mc.vocabulary_id,mv.vocabulary_version,
    mc.invalid_reason IS NULL AND CURRENT_DATE BETWEEN mc.valid_start_date AND mc.valid_end_date AS source_active
  FROM meaning_selected ms LEFT JOIN public.concept mc ON mc.concept_id=ms.concept_id
  LEFT JOIN public.vocabulary mv ON mv.vocabulary_id=mc.vocabulary_id
), meaning_synonyms AS MATERIALIZED (
  SELECT DISTINCT s.requested_id,cs.concept_synonym_name AS term,cs.language_concept_id
  FROM meaning_sources s JOIN public.concept_synonym cs ON cs.concept_id=s.requested_id
  WHERE s.found AND cs.concept_synonym_name IS NOT NULL AND pg_catalog.btrim(cs.concept_synonym_name)<>''
), meaning_synonym_rank AS MATERIALIZED (
  SELECT *,pg_catalog.row_number() OVER (PARTITION BY requested_id ORDER BY term COLLATE "C",language_concept_id) AS item_rank
  FROM meaning_synonyms
), meaning_relations AS MATERIALIZED (
  SELECT ms.requested_id,r.concept_id_1 AS source_id,r.concept_id_2 AS target_id,r.relationship_id,
    r.valid_start_date,r.valid_end_date,r.invalid_reason,
    rm.relationship_name,rm.is_hierarchical,rm.defines_ancestry,rm.reverse_relationship_id,rm.relationship_concept_id,
    ms.source_identity, pg_catalog.jsonb_build_object('concept_id', target.concept_id, 'concept_name', target.concept_name, 'vocabulary_id', target.vocabulary_id, 'concept_code', target.concept_code, 'concept_class_id', target.concept_class_id, 'domain_id', target.domain_id, 'standard_concept', target.standard_concept, 'valid_start_date', target.valid_start_date, 'valid_end_date', target.valid_end_date, 'invalid_reason', target.invalid_reason, 'vocabulary_version', tv.vocabulary_version) AS target_identity,
    target.vocabulary_id AS target_vocabulary_id,tv.vocabulary_version AS target_vocabulary_version,
    pg_catalog.row_number() OVER (PARTITION BY ms.requested_id ORDER BY
      CASE r.relationship_id WHEN 'Is a' THEN 1 WHEN 'Has status' THEN 2 WHEN 'Has finding site' THEN 3 WHEN 'Has interprets' THEN 4 WHEN 'Has interpretation' THEN 5 WHEN 'Has asso morph' THEN 6 WHEN 'Has causative agent' THEN 7 WHEN 'Has severity' THEN 8 WHEN 'Has laterality' THEN 9 WHEN 'Part of' THEN 10 END,
      r.concept_id_2,r.valid_start_date,r.valid_end_date) AS item_rank
  FROM meaning_sources ms JOIN public.concept_relationship r ON r.concept_id_1=ms.requested_id
  JOIN public.concept target ON target.concept_id=r.concept_id_2
  JOIN public.relationship rm ON rm.relationship_id=r.relationship_id
  LEFT JOIN public.vocabulary tv ON tv.vocabulary_id=target.vocabulary_id
  WHERE ms.found AND ms.source_active AND r.relationship_id IN
    ('Is a','Has status','Has finding site','Has interprets','Has interpretation','Has asso morph','Has causative agent','Has severity','Has laterality','Part of')
    AND r.invalid_reason IS NULL AND CURRENT_DATE BETWEEN r.valid_start_date AND r.valid_end_date
    AND target.invalid_reason IS NULL AND CURRENT_DATE BETWEEN target.valid_start_date AND target.valid_end_date
), meaning_bundles AS MATERIALIZED (
  SELECT ms.selection_index,pg_catalog.jsonb_build_object(
    'schema_version','ohdsi_meaning_e4_v1','concept_id',ms.requested_id,
    'source',ms.source_identity,'verification_date',CURRENT_DATE::TEXT,
    'synonyms',COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
       'concept_id',sy.requested_id,'term',sy.term,'language_concept_id',sy.language_concept_id,
       'source_table','concept_synonym','description_type','not_stored_in_source_schema') ORDER BY sy.item_rank)
       FROM meaning_synonym_rank sy WHERE sy.requested_id=ms.requested_id AND sy.item_rank<=12),'[]'::JSONB),
    'relations',COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
       'source_id',mr.source_id,'target_id',mr.target_id,'relationship_id',mr.relationship_id,
       'direction','source_to_target','source_identity',mr.source_identity,'target_identity',mr.target_identity,
       'valid_start_date',mr.valid_start_date,'valid_end_date',mr.valid_end_date,'invalid_reason',mr.invalid_reason,
       'relationship_name',mr.relationship_name,'is_hierarchical',mr.is_hierarchical,'defines_ancestry',mr.defines_ancestry,
       'reverse_relationship_id',mr.reverse_relationship_id,'relationship_concept_id',mr.relationship_concept_id) ORDER BY mr.item_rank)
       FROM meaning_relations mr WHERE mr.requested_id=ms.requested_id AND mr.item_rank<=10),'[]'::JSONB),
    'availability',pg_catalog.jsonb_build_object(
       'concept_identity',CASE WHEN ms.found THEN 'available' ELSE 'not_found' END,
       'synonyms',CASE WHEN EXISTS(SELECT 1 FROM meaning_synonyms sy WHERE sy.requested_id=ms.requested_id) THEN 'available' ELSE 'no_rows' END,
       'direct_relations',CASE WHEN EXISTS(SELECT 1 FROM meaning_relations mr WHERE mr.requested_id=ms.requested_id) THEN 'available' ELSE 'no_rows' END,
       'vocabulary_metadata',CASE WHEN ms.found AND ms.vocabulary_version IS NOT NULL AND pg_catalog.btrim(ms.vocabulary_version)<>''
          AND NOT EXISTS(SELECT 1 FROM meaning_relations mr WHERE mr.requested_id=ms.requested_id AND mr.item_rank<=10
             AND (mr.target_vocabulary_version IS NULL OR pg_catalog.btrim(mr.target_vocabulary_version)='')) THEN 'available' ELSE 'unavailable' END,
       'description_type','not_stored_in_source_schema','formal_text_definition','not_stored_in_source_schema','relationship_group','not_stored_in_source_schema'),
    'caps',pg_catalog.jsonb_build_object('synonyms',12,'direct_relations',10),
    'counts',pg_catalog.jsonb_build_object('synonyms_total',(SELECT pg_catalog.count(*) FROM meaning_synonyms sy WHERE sy.requested_id=ms.requested_id),
       'direct_relations_total',(SELECT pg_catalog.count(*) FROM meaning_relations mr WHERE mr.requested_id=ms.requested_id)),
    'truncated',pg_catalog.jsonb_build_object('synonyms',(SELECT pg_catalog.count(*)>12 FROM meaning_synonyms sy WHERE sy.requested_id=ms.requested_id),
       'direct_relations',(SELECT pg_catalog.count(*)>10 FROM meaning_relations mr WHERE mr.requested_id=ms.requested_id))
  ) AS bundle FROM meaning_sources ms
), meaning_vocabulary_ids AS MATERIALIZED (
  SELECT vocabulary_id FROM meaning_sources WHERE found
  UNION SELECT target_vocabulary_id FROM meaning_relations WHERE item_rank<=10
), all_versions AS MATERIALIZED (
  SELECT * FROM versions UNION
  SELECT v.vocabulary_id,v.vocabulary_name,v.vocabulary_version,v.vocabulary_concept_id
  FROM public.vocabulary v WHERE v.vocabulary_id IN (SELECT vocabulary_id FROM meaning_vocabulary_ids)
)
SELECT pg_catalog.jsonb_build_object(
  'schema_version', 'ohdsi_sql_batch_v1',
  'extension_schema_version','ohdsi_meaning_e4_v1',
  'mode',request.mode,
  'input_request',CASE WHEN request.mode='meaning' THEN pg_catalog.jsonb_build_object('mode','meaning','concept_ids',request.id_values) ELSE pg_catalog.jsonb_build_object('mode','search','terms',request.term_values) END,
  'meaning_scope',pg_catalog.jsonb_build_object('mode',request.mode,'selection_policy',CASE WHEN request.mode='meaning' THEN 'explicit_requested_concept_ids' ELSE 'eligible_exact_standard_or_maps_to_target_by_min_term_index_then_concept_id' END,
     'selected_concept_ids',COALESCE((SELECT pg_catalog.jsonb_agg(concept_id ORDER BY selection_index) FROM meaning_selected),'[]'::JSONB),
     'eligible_count',CASE WHEN request.mode='search' THEN (SELECT pg_catalog.count(*) FROM meaning_auto) ELSE NULL END,'cap',6,
     'truncated',CASE WHEN request.mode='search' THEN (SELECT pg_catalog.count(*)>6 FROM meaning_auto) ELSE FALSE END),
  'meaning_evidence',COALESCE((SELECT pg_catalog.jsonb_agg(bundle ORDER BY selection_index) FROM meaning_bundles),'[]'::JSONB),
  'verification_date', CURRENT_DATE::TEXT,
  'terms', COALESCE((SELECT pg_catalog.jsonb_agg(term ORDER BY term_index)
                     FROM raw_terms), '[]'::JSONB),
  'input_valid', (SELECT valid FROM input_check),
  'cap', 50, 'cap_scope', 'per_term',
  'returned_count', (SELECT pg_catalog.count(*) FROM kept_hits),
  'truncated', COALESCE((SELECT pg_catalog.bool_or(truncated) FROM term_results), FALSE),
  'relationship_cap', 400,
  'relationships_truncated', (SELECT pg_catalog.count(*) > 400 FROM map_probe),
  'term_results', COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(t)
                            ORDER BY term_index) FROM term_results AS t), '[]'::JSONB),
  'retrieval_evidence', COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(k)
                                  ORDER BY term_index, rank) FROM kept_hits AS k), '[]'::JSONB),
  'missing_source_identities', COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(m)
                                  ORDER BY concept_id) FROM missing_source_identities AS m), '[]'::JSONB),
  'concepts', COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(c)
                        ORDER BY concept_id) FROM concepts AS c), '[]'::JSONB),
  'relationships', COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(m)
                             ORDER BY source_id, relationship_id, target_id)
                             FROM maps AS m), '[]'::JSONB),
  'vocabulary_versions', COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(v)
                                   ORDER BY vocabulary_id) FROM all_versions AS v), '[]'::JSONB),
  'safety', pg_catalog.jsonb_build_object(
    'read_only_set_result', safety.ro,
    'transaction_read_only', pg_catalog.current_setting('transaction_read_only'),
    'statement_timeout', safety.statement_timeout,
    'lock_timeout', safety.lock_timeout,
    'statement_timeout_set_by_this_query', FALSE),
  'retrieval_policy', 'exact_synonym_and_name_then_phrase_for_exact_unmatched_terms',
  'exhaustive', FALSE
)::TEXT AS receipt_text
FROM safety CROSS JOIN request
WHERE safety.ro = 'on'
