# BriefForge synthetic research fixtures

All companies, prices, product statements and user feedback in these fixtures are fictional. They demonstrate research workflows, not actual market conditions or procurement advice.

`briefforge.demo.demo_sources()` produces exactly 30 source documents: 29 documents concerning three fictional products and one fictional industry context. Every source carries an explicit disclosure. The public fixture manifest has four development cases and eight frozen diagnostic cases. These cases were reused after bug fixes, so their results are not an untouched estimate of generalization. Differences include billing periods, currencies, an old SSO statement, current enterprise-only SSO, missing disclosures, a same-date contradiction and an instruction planted inside feedback.

`evaluation/frozen_gold.json` contains independently authored evaluation expectations. It is evaluation-only material: `seed_demo`, ingestion and the research engine do not import it. Reference matching checks selected facts and conditions, and has known limitations for paraphrases. It is not a human semantic benchmark.

Evaluation freezes both source-text hashes and the reference-file hash before running. Existing failures are retained on continuation. Every result includes source-bound report snapshots and event logs. Replay results describe deterministic extraction and orchestration behavior, not real model intelligence. Live comparisons must use the shared worker and budget ledger. No source or evaluation result proves actual user time savings.

The final source manifest for this implementation is `artifacts/evaluation-replay-v2/fixture-manifest.json`. The earlier replay directory is retained as a historical pre-revision diagnostic; do not combine results across those manifests.

## Uploadable mixed-format materials

[demo-files](demo-files/) contains exactly 30 fictional materials: five each in TXT, Markdown, HTML, CSV, XLSX and DOCX. Every material contains the original source text and an explicit fictional-data disclosure. [manifest.json](demo-files/manifest.json) preserves the canonical title, competitor, publication date, kind, logical key, source text and hashes; it contains no evaluation answers.

Regenerate with the existing project dependencies, without any model calls:

```sh
python scripts/generate_demo_files.py
```

The generator reads only `demo_sources()` and roundtrips every generated file through the real ingestion function `briefforge.ingest.parse_file(filename, raw)`. It checks that the disclosure and all nonempty source paragraphs survive extraction in their original order. The results are saved to [demo-files-validation.json](../artifacts/demo-files-validation.json). This validates extracted content rather than Office page layout. DOCX visual rendering was unavailable in the delivery environment because LibreOffice was not installed.

To try uploads, create a fictional workspace, select the relevant competitor in the source-library filter, and upload its files. The generic uploader extracts file text; it does not automatically import the metadata manifest or infer competitor ownership. Publication metadata is retained inside each material and in the manifest. These are source materials for ingestion tests, not generated research conclusions.
