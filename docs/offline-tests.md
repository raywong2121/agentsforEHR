# Offline engineering tests

The public package runs without Dify, a database, a model, credentials, or third-party Python packages. Use Python 3.9 or newer from the repository root:

```sh
python3 -m unittest discover -s tests -v
```

To retain a machine-readable receipt, choose a new output name for each attempt:

```sh
python3 tests/run_offline_suite.py --output artifacts/offline-tests.json
```

The runner refuses to overwrite an existing report. The checked-in packaging receipt is `artifacts/offline-tests-public-final.json`; it records 26 passing tests, zero errors, zero failures, eight fresh interpreter probes, and zero service or model calls. Its duration is a local test duration, not workflow latency. Earlier packaging sanity receipts are retained as `artifacts/contracts-first.json` and `artifacts/references-first.json`; both passed. `artifacts/offline-tests-final.json` is an additional passing receipt before replacing the fixture language identifier with an invented TEST identifier. They are not server acceptance results or failed historical model runs.

## What is checked

| Group | Tests | Boundary exercised |
|---|---:|---|
| Stable issue identity | 6 | Note/scope variation does not duplicate a gap; revisions remain separate; actual negative association and equivalence-calibration gaps remain distinct; retries retain their limit; receipt-only changes do not reopen a gap, while relevant new content can |
| Evidence reference and local recovery | 6 | Foreign-binding, stale-vocabulary and stale-source references cannot certify a binding; a legal current negative survives a bad basis reference; an illegal candidate identity cannot invent a semantic negative; a missing row retries only that binding and preserves accepted neighbors |
| Partial target queue | 3 | A partial decision leaves only omitted targets queued; later synthetic decisions still require review; the six-wave cap does not create a seventh wave |
| Strict relation and source immutability | 2 | Passing the reference contract does not establish strict equivalence; runtime bytes stay unchanged during the test |
| Bounded JSON extraction/recovery | 8 | Strict JSON and a single wrapper are accepted; quote-only recovery is audited; duplicate keys, non-finite numbers, truncation, ambiguous wrappers and budget overruns are rejected without returning partial facts |
| Cross-process reference regression | 1 | Eight independent Python processes under different hash seeds produce the same current reference set, snapshot and scope digest; an explicit legacy-hash mutation reproduces order sensitivity |

The 17 contract controls originate from the historical independent E9/E91 suite. The public adaptation uses the E91 reference contract only (`quoted=False`) and does not need the historical `assemble.py`. Tests and fixtures use repository-relative locations. `test_contracts.py` and `test_reference_stability.py` may also be run directly with `--output` to retain their individual reports.

## Fixture derivation and retained fault mechanism

`fixtures/review_ready.json` retains the machine shape of a five-binding, third-party-subject/negation synthetic example. `fixtures/cross_process.json` retains the four-current-binding shape needed to exercise meaning-evidence dictionary insertion order. `fixtures/partial_selection.json` retains two target-level decisions so omission and continuation are tested without an LLM call.

These are **minimized derivative fixtures**, not exact historical state exports. Vocabulary concept IDs, codes, labels, vocabulary names and versions are invented `TEST` values. Unneeded candidates, vocabulary relationships, raw LLM responses, archived model prose, traces and management metadata were removed. No vocabulary release, private chain of thought, real clinical record, database credential or provider secret is distributed. Original archive and original test script hashes, plus current fixture hashes, are listed in `fixtures/PROVENANCE.json`.

The cross-process regression also applies a fixture-only mutation: the current runtime's canonical `content_sha` helper is temporarily replaced in an isolated namespace with insertion-order-sensitive `sha`. The historical fault involved insertion-order-sensitive hashing of evidence objects. On the minimized fixture, the mutation produces two different snapshot digests across the eight seeds, while the unchanged current runtime produces one snapshot and one reference set. This is a causal engineering regression control for the hash-order mechanism; it is not an exact replay of a historical model output, and the released runtime file is never edited.

The historical six-wave state has been replaced with an explicitly constructed exhausted-budget fixture for the wave-limit control. The historical short versus full reviewer quote windows are represented as differing synthetic review notes because the E91 reference contract does not require those quote fields. These adaptations preserve the relevant machine-contract assertions without claiming unchanged clinical semantics.

## What passing does not establish

- A well-formed review can remain medically wrong. Synthetic reviewer replies in this suite are mechanism fixtures, not independent expert labels.
- A downgrade to `label_association` does not prove a concept replacement or a medical semantic repair.
- JSON recovery is a format operation. It does not resolve an omitted fact, wrong subject, incorrect mapping, or unsupported equivalence.
- The package has not executed a new Dify preview, provider request, SQL query, or clinical evaluation during this public packaging task.
- This suite does not reproduce the old E81 failure count, all historical experiment totals, end-to-end task completion, or the blocked E91 live Reviewer validation. Historical paper results remain archival observations with their stated limitations.
