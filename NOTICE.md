# Public Archive and Third-Party Materials Notice

This repository is a public archive of the research source code for coding
Chinese clinical free text into OMOP standard concepts as six-attribute
records. **No open-source license is attached** (`CITATION.cff` carries
`license: NOASSERTION`). Public visibility does not by itself grant rights to
modify, redistribute, sublicense, or use commercially; such uses must be
authorized separately by the rights holder. This notice creates no new
licenses and does not override third-party rights.

## What is published

Source code (`src/`), prompt and deterministic-rule documentation
(`prompts/`), read-only retrieval SQL (`sql/`), and offline contract tests
(`tests/`).

Not included: model or database credentials, API keys, cookies, SSH keys,
patient records, vocabulary database dumps, Dify console management source, or
raw model session transcripts. Model and database endpoints are configured
exclusively through environment variables (see `README.md`).

## OHDSI vocabulary and third-party software

The OHDSI ecosystem and standardized vocabularies involve multiple vocabulary
systems, data, and licenses. Users must independently confirm the access and
use terms of the vocabulary they deploy. This repository distributes no
vocabulary content.

Model services (DeepSeek or the Dify platform) and model weights are subject
to their providers' terms. Archiving configuration parameters here does not
relicense any third-party software.

## Research statement

The offline tests are engineering contract checks (data-independent smoke
tests), not clinical validation. Reported accuracy figures and evaluation
artifacts are not distributed in this repository. The account name in
`CITATION.cff` identifies only the publishing GitHub account; the confirmed
author list belongs to the manuscript.
