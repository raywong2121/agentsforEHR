# OHDSI Evidence Agent

A **research prototype for associating Chinese clinical free text with OHDSI concepts**. It preserves source text and context while performing information decomposition, vocabulary retrieval, expression selection, and review of individual concept bindings within a finite budget. Unresolved issues remain visible in the output. This repository archives the Dify-native workflow, Code node implementations, prompts, SQL, and offline engineering tests for inspection and reproduction.

**Research evidence cutoff: 25 October 2026. Public archive date: 27 October 2026.** This is an experimental implementation, not a clinical deployment product. Passing engineering contracts, successful workflow execution, and model self-review do not replace independent validation of medical meaning. The archive has no independent expert gold standard and does not demonstrate autonomous learning across cases. No credentials, real patient records, or vocabulary database dump are included.

Repository: <https://github.com/raywong2121/agentsforEHR>

## Start here

| Component | Path | Purpose |
|---|---|---|
| Public derivative of the actual exported DSL | [`dify/e91.public.yml`](dify/e91.public.yml) | 30 nodes and 41 edges; derived from an actual export rather than a hand-written blueprint |
| Node and deployment configuration summary | [`dify/configuration_summary.json`](dify/configuration_summary.json) | Inspect model, plugin, Loop, and known contract differences |
| Code node entry points | [`dify/node_entrypoints.json`](dify/node_entrypoints.json) | Inspect actual input variables and wrappers |
| State machine and gates | [`src/term_runtime.py`](src/term_runtime.py) | Code-controlled scheduling, state updates, per-target feedback, and output |
| Bounded JSON recovery | [`src/rule_json.py`](src/rule_json.py) | Extract constrained JSON from model text without external services |
| Database result normalization | [`src/receipt_normalizer.py`](src/receipt_normalizer.py) | Pass query results to the runtime for verification |
| Prompts for four model roles | [`prompts/PROMPTS.md`](prompts/PROMPTS.md) | SourceAudit, Decomposer, Planner, and Reviewer |
| Fixed parameterized read-only queries | [`sql/vocabulary_search.sql`](sql/vocabulary_search.sql) | Query the deployer's existing OHDSI vocabulary database |
| Fully synthetic inputs | [`examples/synthetic_cases.json`](examples/synthetic_cases.json) | Two downstream development cases; not expert reference answers |
| Offline engineering controls | [`tests/`](tests/) | Run without accessing models or databases |
| Offline test results and scope | [`docs/offline-tests.md`](docs/offline-tests.md), [`artifacts/offline-tests-public-final.json`](artifacts/offline-tests-public-final.json) | 26 unittest tests: 17 contract controls, 8 JSON recovery controls, and 1 reference-stability control using eight subprocesses |
| Mechanism and evidence boundaries | [`docs/architecture.md`](docs/architecture.md), [`docs/research-status.md`](docs/research-status.md) | Distinguish implementation, historical observations, and unverified capabilities |

## Run the offline checks

The offline source and tests use only the Python standard library. They require no pip dependencies and do not start services or access a database. With an existing Python 3.9 or later installation, run the following from the repository root. The test receipt records the version actually used.

```sh
python3 -m unittest discover -s tests -v
```

To save a test receipt with execution environment details and individual test results:

```sh
python3 tests/run_offline_suite.py --output artifacts/offline-tests.json
```

The receipt documents these offline engineering controls. The historical set of 17 contract controls has a separately defined scope; additional JSON fixtures and subprocess controls are not additional medical validation. The runner refuses to overwrite an existing receipt. Use a different output filename for each repeat run and retain initial failures.

The actual offline run at publication passed **26/26 tests**. The reference-stability test includes eight independent Python subprocesses. See the receipt linked above for full results and environment details, and `docs/offline-tests.md` for scope. This was not a new model evaluation or an end-to-end Dify acceptance test of E91.

The preparation node can also be checked independently. This command executes neither LLM calls nor SQL:

```sh
PYTHONPATH=src python3 - <<'PY'
import json
from pathlib import Path
from term_runtime import prepare

case = json.loads(Path("examples/synthetic_cases.json").read_text())["cases"][0]
result = prepare(case["case_text"], case["question"], case["strategy_memory_str"])
print("prepare outputs:", sorted(result))
PY
```

The repository has no standalone network runner that calls cloud models and databases independently of Dify. These commands do not establish successful DSL import or acceptance of the end-to-end model workflow.

## Reproduce in an existing Dify installation

1. Import `dify/e91.public.yml` into an independent, unpublished draft. The file derives from the actual E91 export. Only the application name, description, and tenant-specific icon URL were cleaned for public archiving. The repository's provenance manifests document the original export and public derivative.
2. Verify that the target environment supports Workflow, Loop, LLM, Code, variable assignment, and error branches. The YAML format version is `0.7.0`; this does not guarantee compatibility with every Dify version.
3. Configure model services and credentials in your own Dify workspace. All four archived LLM nodes use the configured alias `qwen3.8-max-0902`, temperature `0.2`, output budget `8192`, and `reasoning_effort=low`. These are archived configuration aliases and parameters; they do not establish the identity of upstream model weights or current service availability. Record a new experiment batch when changing a model or its parameters.
4. Verify the archived plugins: `langgenius/openai_api_compatible` **0.0.69** and `spance/db_client_node` **0.1.47**. Full plugin identifiers are retained in the DSL and configuration summary. Do not guess replacement identifiers.
5. Rebind the database tool to your existing read-only vocabulary connection. `postgres_node_5` is a tool name from the original workspace, not a universal built-in connection. This repository supplies no host configuration, account credentials, passwords, model keys, or complete vocabulary database. See `sql/vocabulary_search.sql` for the fixed SQL.
6. Preview the synthetic text in `examples/synthetic_cases.json` first. Inspect the latest Loop state, actual query results, binding revisions, and unresolved targets node by node. Keep the draft unpublished during verification.

Import, reauthorization, and connection rebinding remain required deployment steps; offline tests do not replace them. Cloud models receive text bound to LLM nodes. Hosting Dify on your own server does not mean that text stays on that server. Continue to use synthetic text for initial reproduction.

**Known contract difference, retained in this public archive:** the runtime accepts `max_terms` up to 8, while the SQL typed-search route permits at most 6 terms and the legacy-array route at most 8. The default of 6 fits both routes. Preserve this difference when reproducing archived experiments; any contract correction requires a separate version and tests.

## Capabilities and limitations

- Code controls global tasks and budgets. Models produce decompositions, queries, expression comparisons, or review results within the current task; they do not freely invoke arbitrary external tools.
- Source text, subject, negation, time, numeric values, and the entities to which qualifiers attach must be preserved together. Attaching a `concept_id` to text does not complete coding. A model's label association does not establish strict medical equivalence or loading into OMOP tables. The archive does not establish complete information capture or expert-level accuracy.
- A retrieval miss means only that a particular query returned no candidates; it does not establish that a concept does not exist. Unresolved issues, weaker relation assignments, format recovery, and speed improvements are reported separately and are not counted as corrections of medical meaning.
- Bounded feedback within a single run is implemented: additional retrieval, local reselection, fact revision, and renewed review. `action_priority` and `focus_fields` currently undergo input validation only; only `max_terms` affects the actual budget. Proposed strategies are not automatically promoted, shared persistently, or learned across cases.
- Historical E91 engineering contract controls passed, but new Reviewer calls were blocked by provider access denial, and the related MAIN comparison runs were not executed. Public source availability does not demonstrate that this feedback loop passed a new model evaluation. See the [research status](docs/research-status.md).

## Licensing, citation, and data

No open-source license has been attached to this repository. Public visibility does not grant unrestricted permission to redistribute, modify, or use the code commercially; see [`NOTICE.md`](NOTICE.md). Licensing terms for individual source vocabularies in OHDSI apply independently. This repository does not distribute a complete vocabulary database or restricted vocabulary content.

Software citation metadata is provided in [`CITATION.cff`](CITATION.cff). It identifies the software archive without inventing a paper DOI, journal publication, or research author list. Consult the actual experiment receipts and research article for data and failure boundaries. Test counts must not be interpreted as clinical accuracy.
