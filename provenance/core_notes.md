# Core source preparation record

Prepared on 2026-10-27 from the archived 2026-10-25 E91 actual Dify export. This publication preparation did not import a workflow, call a model, access a database, or establish new clinical performance.

## Public derivative and source identity

`dify/e91.public.yml` is a public derivative of the real exported DSL, not a fabricated blueprint and not a byte-identical copy of the private archive. The archived export SHA-256 is `26f373d6ce1ff8772950a77ca89cdaf8fb21ac9b61584402b916e3becc7d9164`. Exactly three scalar values were changed: a neutral app name, an accurate public description, and an empty database provider icon in place of a workspace-specific tenant icon URL. No node, edge, model parameter, prompt, Code implementation, SQL statement, variable selector, or plugin identifier was changed. Structural comparison is recorded in `core_preparation_checks.json`.

The archive contains 30 nodes and 41 edges. Its four LLM nodes use the configured alias `qwen3.8-max-0902` through `langgenius/openai_api_compatible/openai_api_compatible`, temperature 0.2, maximum output budget 8192, and reasoning effort `low`. These are archived Dify configuration values, not a guarantee of upstream weight identity or present model availability. Both plugin pins are retained exactly as exported: `spance/db_client_node` 0.1.47 and `langgenius/openai_api_compatible` 0.0.69, including full marketplace identity hashes in the YAML and configuration summary. A configuration pin does not independently prove the version loaded by a live process.

## Readable source and embedded nodes

`src/term_runtime.py` and `src/rule_json.py` are byte-identical copies of their archived sources. `src/receipt_normalizer.py` is a filename-only rename of `normalize.e4.py`; its content is byte-identical. These modules use the Python standard library. The ten general Code nodes embed the exact rule JSON recovery module and runtime text. The remaining Code node embeds the exact receipt normalizer. `dify/node_entrypoints.json` exposes the actual wrappers and input/output selectors; it is a structural source extract, not an execution log.

`prompts/PROMPTS.md` is the byte-identical source prompt document. The actual SYSTEM prompt at each of the four LLM nodes is the document's common introduction followed by that node's SYSTEM section. `prompts/actual_node_prompts.json` preserves all eight actual prompt texts extracted from the final exported DSL. These files contain public prompt instructions, not model-generated private reasoning.

`sql/vocabulary_search.sql` is byte-identical to the exported fixed SQL statement. It reads the OHDSI vocabulary tables `public.concept`, `public.concept_synonym`, `public.concept_relationship`, `public.relationship`, and `public.vocabulary`. Vocabulary data are not distributed in this repository. Users must supply vocabulary data under their own applicable access and license arrangements.

## Configuration that remains external

The export contains no model API credential or database host, port, username or password. The database node's archived tool name `postgres_node_5` refers to a workspace-defined connection tool and must be rebound to an authorized connection in a destination workspace. Its name is retained to document the real exported wiring; it does not create that connection. Model aliases and provider availability must also be configured in the destination Dify workspace. No management scripts, management sessions, API tokens, raw run logs, account identities, or new network services are included by this preparation step.

The SQL explicitly requests transaction-local read-only operation. A separately configured read-only database role and operational query timeouts remain deployment responsibilities; this source archive does not grant or verify those permissions. Local tests do not establish successful destination-workspace import or database access.

## Preserved limits and known contract risk

The runtime defaults to six Loop waves, three shared search/meaning batches, six search terms per batch, and at most two fact-repair waves. Runtime `strategy_memory_str.max_terms` accepts values through eight, whereas the exported SQL accepts at most six terms in a typed `search` object; only the legacy bare-array form accepts eight. This is an existing cross-component contract risk, preserved rather than silently fixed during publication. The default six is within both typed-search contracts. Meaning requests accept at most six concept IDs. Source locations are `src/term_runtime.py` lines 11–12 and 220–223, and `sql/vocabulary_search.sql` lines 30–43.

`action_priority` and `focus_fields` are validated input keys but are not used by the archived scheduler to change its actions. Only `max_terms` changes the prepared budget. The emitted `policy_candidate` is an unpromoted candidate list; this implementation does not automatically persist or learn a shared cross-case strategy.

## Synthetic examples and evidence boundary

`examples/synthetic_cases.json` contains two archived synthetic source texts (N1 and N2) extracted from the research analysis, their source hashes, and a clearly marked example research question. They are not real patient records, expert gold labels, or new E91 full-start executions. Passing a Code contract, recovering JSON, matching an exact quote, or calibrating an existing concept's relation strength does not establish a new medically correct concept mapping. The source archive leaves those limitations visible.
