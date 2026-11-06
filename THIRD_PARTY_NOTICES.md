# Third-party notices

`scripts/sc_clean.py` re-integrates ideas and code paths from two upstream
skills. Both are permissively licensed; the notices and licence texts are
included here as required by their terms.

## 1. K-Dense `scanpy` skill

- Source: https://github.com/K-Dense-AI/scientific-agent-skills (`skills/scanpy`)
- Copyright: K-Dense Inc.
- The skill's frontmatter declares **BSD-3-Clause**; the repository's top-level
  `LICENSE.md` is MIT. This project redistributes the derived parts under the
  more restrictive of the two, BSD-3-Clause.
- Full text: [`LICENSES/BSD-3-Clause.txt`](LICENSES/BSD-3-Clause.txt)
- Used for: count validation, explicit counts-layer selection, gene-class
  annotation, pseudobulk aggregation.

## 2. Anthropic `single-cell-rna-qc` skill

- Source: https://github.com/anthropics/knowledge-work-plugins (`bio-research/skills/single-cell-rna-qc`)
- Copyright: Anthropic
- License: **Apache-2.0**
- Full text: [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt)
- Used for: MAD outlier detection and the before/after QC summary.

## Changes made in the re-integration

- scanpy is optional; only `qc --scrublet` imports it.
- QC flags cells (`pass_qc`, `qc_reason`) instead of dropping them.
- MAD thresholds are computed per batch when `--group-by` is given.
- Every stage appends a JSONL cleaning-ledger row.

Nothing else from the upstream repositories is redistributed.
