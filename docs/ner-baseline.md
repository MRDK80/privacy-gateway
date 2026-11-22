# Synthetic RU and EN NER baseline

This protocol freezes the quality and resource criteria for the local NER
candidate in issue #428. The baseline uses the current detector and a frozen,
synthetic dictionary. It calls the same detection function used by the CLI
and does not need a keyring, network access, or model. The report contains
aggregate counts and optional timings.

## Corpus and labels

The fixtures under `tests/fixtures/ner/` are project-authored and synthetic.
Use `tuning.jsonl` (16 documents) for iteration. Keep `held-out.jsonl`
(18 documents) for evaluation after the candidate and its settings are fixed.
The splits use disjoint documents and labeled names. They include unfamiliar
names, sentences with no entities, and dictionary hits in both entity and
non-entity contexts. Held-out documents also include repeated names and names
whose prefixes overlap dictionary entries. Both splits include Russian and
English documents with a clause in the other language.

Each JSON Lines record defines a document ID, its surrounding text's language
(`ru` or `en`), its text, and a list of `[start, end, type]` annotations.
Offsets count Unicode code points from zero, with the end exclusive. Annotate
the full name as written, including any inflected ending or legal or
organizational designation that forms part of the name, excluding surrounding
punctuation and determiners. Count each occurrence separately. An ambiguous
string is labeled only when it refers to a person, organization or location in
context. For mixed documents, both gold and predictions use the document's
language, including entities in the embedded clause. These rows measure
mixed-text behavior within the surrounding language bucket.

Keep `dictionary.yaml` fixed for both detectors. The fixture includes known
entries from both splits to exercise intentional dictionary overlap, so these
entries are available as dictionary inputs in both runs. Do not add corpus
surface forms to it or use the held-out annotations for threshold, model,
feature or policy selection.

## Exact scoring

A prediction is a TP only if its document, label and both offsets match a gold
annotation exactly. A shorter or longer prediction yields an FP for the
prediction and an FN for the missed gold span. A wrong type yields an FP for
the predicted type and an FN for the gold type. Other detection types are out
of scope for scoring. Identical span/type records in one document count once.
Matching spans in different documents count independently.

Sum TP, FP and FN separately for each document language × entity type before
dividing. Precision is TP / (TP + FP) and recall is TP / (TP + FN). A zero
denominator gives JSON `null`. An observed zero with a nonzero denominator is
`0.0`. Compare unrounded fractions against thresholds.

## Reproduce the baseline

From the repository root with the project installed in a development
environment, run:

```bash
python -m tools.ner_baseline --split tuning
python -m tools.ner_baseline --split held-out
```

The held-out results for detector source
`ce17499f81537dfd1bd51cc47c4ed5a28bc8daba` are:

| Language | Type | TP | FP | FN | Precision | Recall |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| RU | PERSON | 1 | 3 | 5 | 0.25 | 1/6 |
| RU | ORG | 1 | 1 | 3 | 0.50 | 0.25 |
| RU | LOC | 0 | 0 | 4 | undefined | 0 |
| EN | PERSON | 1 | 3 | 5 | 0.25 | 1/6 |
| EN | ORG | 1 | 1 | 3 | 0.50 | 0.25 |
| EN | LOC | 0 | 0 | 4 | undefined | 0 |

The detector recognizes known PERSON and ORG strings, including some uses
outside those categories. It misses unfamiliar names and has no LOC detector.

## Candidate acceptance criteria

Require precision ≥ 0.80 and recall ≥ 0.75 independently in all six held-out
rows. An undefined metric fails its threshold. These floors require gains
over the baseline in all six rows and limit false positives from context-free
matching. Given the small number of labeled spans in each bucket, passing
establishes performance only on this frozen corpus.

Measure resources on the reference CPU, an AMD Ryzen 5 5600 (Linux x86-64),
with no accelerator and a process pinned to one logical CPU. Load config and
any model once in a fresh process. After one untimed detection pass over the
held-out split, time 100 more passes in fixture order. Keep the detector and
its runtime in the same process. Record CPU model, OS, Python version, model
version and individual run results. Run three fresh processes and require
each run to meet:

| Resource | Maximum | Measurement |
| --- | ---: | --- |
| CPU | 75 ms/document | Process CPU time for timed passes divided by number of calls |
| Peak resident memory | 1792 MiB (1.75 GiB) | Maximum RSS for the complete process, including model initialization |
| Warm latency | 95 ms/document | Nearest-rank p95 wall time over individual detection calls |

The owner approved this budget for #428 on 2026-11-22 after the #469
measurements. ADR-469 preserves the original #429 limits (30 ms CPU per
document, 50 ms warm p95 and 512 MiB peak RSS) and the alternative used for
that experiment.

These limits define a single-core interactive evaluation budget for the
fixture workload. Warm latency includes the detection call and excludes model
startup, file input, scoring and output. Peak RSS includes startup. Apply the
same procedure to candidate and baseline. For the baseline, run the following
on Linux, substituting an allowed logical CPU ID for `0` if needed:

```bash
for run in 1 2 3; do
  taskset -c 0 /usr/bin/time -f 'peak_rss_kib=%M' \
    python -m tools.ner_baseline --split held-out --timing
done
```

The timing workload uses short documents from the quality corpus. It does not
establish a production latency bound for larger documents or model cold start.
