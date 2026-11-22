# Preregistration for the next bilingual NER experiment

This experiment follows the rejection in [ADR-430](ADR-430-local-ner.md).
It asks whether two small language-specific CPU models improve recognition of
unfamiliar PERSON and ORG spans while retaining LOC quality and the current
detector's findings. Paired scores distinguish the models' recognition errors
from losses caused by fixed dictionary findings and overlap precedence. The
candidate is prepared for later evaluation. No new holdout has been evaluated.

## What the exposed holdout can establish

In the additive mode, every baseline FP remains an FP. A gold span missed by
the baseline is unreachable if it intersects any preserved baseline span.
For each language and label, let *G* be the gold count, *F* the retained baseline
FP count and *B* the count of such blocked gold spans. Even a model that adds
every reachable gold span and no FP can reach at most
precision `(G - B) / (G - B + F)` and recall `(G - B) / G`, using the
[#429](ner-baseline.md) zero-denominator rules. To reproduce the ceiling,
run the baseline on each document and count missed exact gold spans with
`gold_start < baseline_end and baseline_start < gold_end`, including baseline
types outside PERSON, ORG and LOC. The old holdout's gold spans are mutually
non-overlapping within each document.

| Old held-out language | Type | Gold | Fixed FP | Blocked gold | Best precision | Best recall |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| RU | PERSON | 6 | 3 | 2 | 4/7 | 4/6 |
| RU | ORG | 4 | 1 | 0 | 4/5 | 1 |
| RU | LOC | 4 | 0 | 0 | 1 | 1 |
| EN | PERSON | 6 | 3 | 2 | 4/7 | 4/6 |
| EN | ORG | 4 | 1 | 0 | 4/5 | 1 |
| EN | LOC | 4 | 0 | 0 | 1 | 1 |

The two blocked PERSON spans in each language are repeated longer names in
`ru-09` and `en-09` whose prefixes are baseline findings. Even with those
spans recovered, keeping three false positives caps PERSON precision at
`6/9`. No candidate can qualify on that holdout under this merge rule.
Keep the published holdout and its results as historical evidence. Treat any
structural failure on the fresh holdout as an experiment result and retain
all its cases and labels.

## Frozen candidate and evaluation rules

Use spaCy 3.8.18 on CPU with Explosion's
[`ru_core_news_sm` 3.8.0](https://github.com/explosion/spacy-models/releases/tag/ru_core_news_sm-3.8.0)
and
[`en_core_web_sm` 3.8.0](https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0).
Keep the native tokenizers and `tok2vec` and `ner` components. Exclude the
other components when loading. The language-specific models make this a
different recognition experiment from the multilingual WikiNER pipeline.

- Run both models on each whole document for RU, EN and mixed input. Use
  neither the gold language bucket nor script segmentation to route input.
  Load both models into the same process. For RU map `PER`, `ORG` and `LOC`
  to `PERSON`, `ORG` and `LOC`. For EN map `PERSON`, `ORG`, `LOC` and
  `GPE` to `PERSON`, `ORG`, `LOC` and `LOC`. Ignore the other labels.
  Use original Unicode character offsets and no confidence or dictionary
  filter. Abort the run on invalid offsets, missing dependencies, model
  load failure or inference errors.
- For **model-only**, resolve both models' mapped spans together. Visit
  longer spans first, then smaller start, then RU model before EN and
  finally label in lexical order. Accept a span if it overlaps no span
  already accepted. This also deduplicates exact matching predictions.
- For **combined**, preserve every baseline finding from the same current
  detector and frozen #429 dictionary. Drop model proposals intersecting
  any baseline finding before resolving the remaining proposals with the
  same model-order rule. Include all legacy regex and secret findings when
  checking overlaps. Do not suppress or relabel baseline findings.
- Print paired baseline, model-only and combined TP, FP, FN, precision and
  recall for each language × entity type and include the combined-mode
  ceilings. Keep the [#429](ner-baseline.md) exact scoring, undefined metric
  handling and acceptance unchanged. Only combined-mode held-out quality
  and combined-mode resources count for acceptance. Record the model-only
  and mixed-document results as diagnostics.
- Apply the same three pinned single-core CPU runs, 100 warm passes after
  an untimed pass, mean CPU per document, nearest-rank p95 and full-process
  RSS from #429. Include both models in each process and in cold load time.
  The limits stay at 30 ms mean CPU per document, 50 ms warm p95 and 512 MiB
  peak RSS. Each run must meet all three.

## Offline artifact checks before running

The publisher and staged wheels declare MIT for both pipelines. The RU wheel's
metadata lists Nerus as MIT. The EN wheel's metadata lists OntoNotes 5 as
commercial, licensed by Explosion, ClearNLP conversion as a citation with
no ClearNLP code packaged, and WordNet 3.0 under its named license. Check the
bundled LICENSE, attribution and upstream source terms before admitting the
wheels for evaluation. Record
any source or redistribution restrictions separately from the model license.
The preregistration makes no decision about redistribution.

Use trusted release wheels staged with all resolved dependencies in a local
wheelhouse. Record the environment versions and verify both wheel hashes
against the publisher before installing with pip `--no-index --find-links`
in a separate venv. Confirm its versions, NER labels and retained components
using only synthetic smoke inputs before the sealed corpus is opened. Use
`pip check` to verify dependencies. Load locally through spaCy with CPU
selected. Do not install dependencies or fetch model data at inference time.

| Wheel | Expected SHA-256 |
| --- | --- |
| `ru_core_news_sm-3.8.0-py3-none-any.whl` | `69978d47b43e2c4f329bebdb155e8e9d3861bba1a58ba25551419dae7d7e07fc` |
| `en_core_web_sm-3.8.0-py3-none-any.whl` | `1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85` |

## Fresh holdout handoff

1. Have an independent corpus author create and label 40 synthetic documents
   per surrounding language, including 10 mixed-language documents per bucket.
   Require at least 24 labeled occurrences of each entity type in each language,
   at least four entity-free documents per bucket, and examples of positive
   and negative dictionary matches, boundary overlaps, and repeated entities.
   Include unfamiliar names in each type. The author may refer to the frozen
   dictionary for intentional collisions and must keep documents, sentence
   templates and other labeled surfaces disjoint from both #429 splits.
2. Use the #429 JSONL layout and exact half-open Unicode annotations. An
   independent reviewer verifies labels, offsets, coverage and disjointness
   before the corpus custodian freezes the file and records its SHA-256.
   Do not share raw fresh cases or labels with the evaluator author until
   the evaluation source revision, artifact hashes and rules are frozen.
   The corpus author and reviewer receive no candidate predictions.
3. Develop and verify the evaluation harness with synthetic smoke cases and
   the #429 tuning split only. A controller runs the frozen harness on the
   sealed corpus and records source and corpus digests, counts, ceilings and
   resource results. Report an infeasible ceiling or a failed threshold as
   measured. Do not rebuild the holdout or alter overlap policy or thresholds
   in response to the outcome. Any model or policy selected for a later
   experiment needs its own preregistration and fresh holdout.

## Evaluation command for the corpus custodian

Set `wheelhouse` to the verified local wheelhouse and `eval_python` to the
Python executable in the separate evaluation venv. Verify the release hashes
above before installation. From the repository root:

```bash
printf '%s  %s\n' \
  69978d47b43e2c4f329bebdb155e8e9d3861bba1a58ba25551419dae7d7e07fc \
  "$wheelhouse/ru_core_news_sm-3.8.0-py3-none-any.whl" \
  1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85 \
  "$wheelhouse/en_core_web_sm-3.8.0-py3-none-any.whl" | sha256sum --check &&
"$eval_python" -m pip install --no-index --find-links "$wheelhouse" \
  spacy==3.8.18 numpy==2.5.5 thinc==8.3.14 blis==1.3.4 \
  cryptography==50.0.2 keyring==25.7.0 PyYAML==6.0.4 \
  "$wheelhouse/"{ru_core_news_sm,en_core_web_sm}-3.8.0-py3-none-any.whl &&
"$eval_python" -m pip check
```

Set `corpus` to the sealed JSONL path and `digest` to the independently
recorded SHA-256. Each JSONL record should include `"mixed": true` for
mixed-language input and `"mixed": false` otherwise. That field controls
diagnostic scoring only. The evaluator verifies the corpus digest before
loading either model. Run this command only after the preregistered handoff:

```bash
export PYTHONPATH=src OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
for run in 1 2 3; do
  taskset -c 0 /usr/bin/time -f 'peak_rss_kib=%M' \
    "$eval_python" -m tools.ner_bilingual_candidate \
    --corpus "$corpus" --expect-sha256 "$digest" --timing
done
```
