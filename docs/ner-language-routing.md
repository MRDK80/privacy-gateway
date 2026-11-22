# Sentence segmentation and script routing for bilingual NER

[#445](https://github.com/MRDK80/privacy-gateway/issues/445) compares three
evaluation variants on the 80 documents disclosed by [the second #428
experiment](https://github.com/MRDK80/privacy-gateway/issues/428#issuecomment-7074092997).
This development sample informs #446. Quality on a fresh blind corpus and a
separate owner decision are required before integration.

## Rule fixed before evaluating the sentence variants

The full-document control runs both models on the complete text. The second
variant runs both models on every sentence. The routed variant chooses models
per sentence using the rule below. Use the runtime, models, loaded components,
label mappings, baseline detector, and proposal order from the
[previous protocol](ner-next-experiment.md). Apply the frozen dictionary and
regex detector to the original text in all three variants. Resolve model
proposals once per document against those findings.

Split after a run of `.`, `!`, `?` or `…` when followed by whitespace or
the end of input. Include any adjacent closing quotes or brackets with the
preceding sentence. Split on CRLF, CR and LF regardless of terminal
punctuation. Keep other punctuation within the segment. Trim whitespace from
segment edges and skip empty segments. The splitter has no abbreviation
dictionary. A period followed by whitespace can split an abbreviation.
Preserve original Unicode code-point offsets by adding the segment's start
to each native model offset.

Count Unicode letters whose names begin with `CYRILLIC ` or `LATIN ` within
each sentence. Count each letter once. Ignore punctuation, digits, combining
marks and letters in other scripts.

- With zero supported letters, send the sentence to both models as undetermined.
- With one to three supported letters, send it to both as short.
- Otherwise, send it to the Russian or English model when the corresponding
  script supplies at least 80% of the supported letters. A sentence with
  letters in both scripts still counts as mixed for routing diagnostics.
- If neither script supplies 80%, send the full sentence to both models as
  mixed without a dominant script. The models receive the whole sentence when
  the language changes mid-sentence. A minority-script name can therefore be
  passed only to the dominant-script model.

The router sees only sentence text. The document language and mixed marker
from the corpus are used to score outcomes. Count routed sentences with
mixed scripts, short input or no supported letters as uncertain choices.
These counts describe script evidence and fallbacks. Sentence language
annotations are unavailable, so the counts do not measure routing accuracy.

## Quality on the disclosed development sample

The sample has 40 documents per surrounding language, including 10 mixed
documents per language. Scores use the surrounding language bucket for
every occurrence. Each cell gives **TP / FP / FN** followed by **precision /
recall** rounded to three decimal places. Threshold decisions use unrounded
ratios. The full-document control reproduced all six published #428 rows.

### All 80 documents

| Language | Type | Full document | Both per sentence | Routed per sentence | Both Δ counts | Routed Δ counts |
| --- | --- | --- | --- | --- | --- | --- |
| RU | PERSON | 19 / 24 / 8 · 0.442 / 0.704 | 19 / 24 / 8 · 0.442 / 0.704 | 23 / 13 / 4 · 0.639 / 0.852 | 0 / 0 / 0 | +4 / -11 / -4 |
| RU | ORG | 14 / 10 / 12 · 0.583 / 0.538 | 14 / 10 / 12 · 0.583 / 0.538 | 14 / 6 / 12 · 0.700 / 0.538 | 0 / 0 / 0 | 0 / -4 / 0 |
| RU | LOC | 18 / 6 / 8 · 0.750 / 0.692 | 18 / 6 / 8 · 0.750 / 0.692 | 20 / 3 / 6 · 0.870 / 0.769 | 0 / 0 / 0 | +2 / -3 / -2 |
| EN | PERSON | 13 / 11 / 14 · 0.542 / 0.481 | 13 / 10 / 14 · 0.565 / 0.481 | 15 / 11 / 12 · 0.577 / 0.556 | 0 / -1 / 0 | +2 / 0 / -2 |
| EN | ORG | 12 / 27 / 14 · 0.308 / 0.462 | 12 / 29 / 14 · 0.293 / 0.462 | 15 / 23 / 11 · 0.395 / 0.577 | 0 / +2 / 0 | +3 / -4 / -3 |
| EN | LOC | 6 / 1 / 20 · 0.857 / 0.231 | 6 / 1 / 20 · 0.857 / 0.231 | 7 / 2 / 19 · 0.778 / 0.269 | 0 / 0 / 0 | +1 / +1 / -1 |

### 60 monolingual documents

| Language | Type | Full document | Both per sentence | Routed per sentence |
| --- | --- | --- | --- | --- |
| RU | PERSON | 13 / 15 / 4 · 0.464 / 0.765 | 13 / 15 / 4 · 0.464 / 0.765 | 15 / 8 / 2 · 0.652 / 0.882 |
| RU | ORG | 9 / 5 / 7 · 0.643 / 0.562 | 9 / 5 / 7 · 0.643 / 0.562 | 9 / 3 / 7 · 0.750 / 0.562 |
| RU | LOC | 13 / 5 / 3 · 0.722 / 0.812 | 13 / 5 / 3 · 0.722 / 0.812 | 15 / 2 / 1 · 0.882 / 0.938 |
| EN | PERSON | 6 / 7 / 11 · 0.462 / 0.353 | 6 / 7 / 11 · 0.462 / 0.353 | 7 / 7 / 10 · 0.500 / 0.412 |
| EN | ORG | 8 / 20 / 8 · 0.286 / 0.500 | 8 / 20 / 8 · 0.286 / 0.500 | 11 / 16 / 5 · 0.407 / 0.688 |
| EN | LOC | 1 / 0 / 15 · 1.000 / 0.062 | 1 / 0 / 15 · 1.000 / 0.062 | 2 / 1 / 14 · 0.667 / 0.125 |

### 20 mixed documents

| Language | Type | Full document | Both per sentence | Routed per sentence |
| --- | --- | --- | --- | --- |
| RU | PERSON | 6 / 9 / 4 · 0.400 / 0.600 | 6 / 9 / 4 · 0.400 / 0.600 | 8 / 5 / 2 · 0.615 / 0.800 |
| RU | ORG | 5 / 5 / 5 · 0.500 / 0.500 | 5 / 5 / 5 · 0.500 / 0.500 | 5 / 3 / 5 · 0.625 / 0.500 |
| RU | LOC | 5 / 1 / 5 · 0.833 / 0.500 | 5 / 1 / 5 · 0.833 / 0.500 | 5 / 1 / 5 · 0.833 / 0.500 |
| EN | PERSON | 7 / 4 / 3 · 0.636 / 0.700 | 7 / 3 / 3 · 0.700 / 0.700 | 8 / 4 / 2 · 0.667 / 0.800 |
| EN | ORG | 4 / 7 / 6 · 0.364 / 0.400 | 4 / 9 / 6 · 0.308 / 0.400 | 4 / 7 / 6 · 0.364 / 0.400 |
| EN | LOC | 5 / 1 / 5 · 0.833 / 0.500 | 5 / 1 / 5 · 0.833 / 0.500 | 5 / 1 / 5 · 0.833 / 0.500 |

The `Δ` columns subtract the full-document TP, FP and FN counts in that order.
Applying segmentation alone changes three false positives in mixed English
documents across the two affected types. It changes no TP or FN. Routing the
sentences cuts false positives from 79 to 58 and recovers 12 true positives
relative to the full-document control. RU PERSON improves on both subsets.
EN ORG improves among monolingual documents and keeps its original mixed
counts. Both LOC gains come from monolingual documents. EN LOC gains one TP
and one FP, dropping precision from 0.857 to 0.778. Its recall stays low
at 0.269. RU LOC alone reaches the [#429 quality floors](ner-baseline.md#candidate-acceptance-criteria)
of precision ≥ 0.80 and recall ≥ 0.75 in the routed variant.

### Routing and overlap diagnostics

The router evaluated 100 sentences. It selected RU on 42 with only Cyrillic
letters and EN on 45 with only Latin letters. The 13 uncertain decisions
were all mixed-script sentences. One went to RU, two went to EN, and ten
went to both. No sentence in this corpus reached the short or undetermined
case. Unit tests exercise both fallbacks.

Error categories count each scored FP once. An exact gold boundary with a
different type takes precedence, followed by an overlap with the same type,
an overlap with a different type, and no overlap. Discarded proposals also
include duplicate proposals. A gold merge loss requires an exact mapped
model proposal that was absent after merging. Assign the loss to baseline
if that span overlaps a preserved finding, and to model precedence otherwise.

| Diagnostic | Full document | Both per sentence | Routed per sentence |
| --- | ---: | ---: | ---: |
| FP with wrong type only | 32 | 31 | 34 |
| FP with wrong boundary only | 20 | 20 | 12 |
| FP with wrong type and boundary | 10 | 10 | 4 |
| FP with no gold overlap | 17 | 19 | 8 |
| Exact gold blocked by a preserved baseline span | 2 | 2 | 2 |
| Exact gold blocked by model precedence | 15 | 15 | 1 |
| Discarded model proposals | 82 | 82 | 19 |

Routing reduces the number of exact gold proposals discarded by model
precedence from 15 to 1. It produces 34 exact-boundary type errors, two
more than the control. A prediction error can overlap more than one gold
span, so these diagnostics count predictions and merge losses separately.

## Resource measurements

The reference host used an AMD Ryzen 5 5600, Linux 7.0.0-43-generic x86-64,
Python 3.12.3, Unicode data 15.0.0, spaCy 3.8.18, NumPy 2.5.5, Thinc 8.3.14,
and Blis 1.3.4. Each variant ran in three fresh CPU-only processes, pinned to
logical CPU 0 with numerical library threads set to one. Each process loaded
the dictionary and both models, completed one untimed pass, and timed 100
further passes in fixture order (8,000 calls). Warm CPU and nearest-rank wall
p95 include segmentation, routing where applicable, model calls, baseline
detection and merge. Scoring and error accounting run outside the timed loop.
Cold load includes importing spaCy and constructing both pipelines. Peak RSS
covers the whole process.

| Variant | Run | Cold model load (ms) | Mean CPU (ms/doc) | Warm p95 (ms/doc) | Peak RSS (KiB) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `full` | 1 | 1224.833 | 6.018 | 7.666 | 335976 |
| `full` | 2 | 1304.501 | 5.975 | 7.300 | 335904 |
| `full` | 3 | 1268.614 | 5.799 | 6.881 | 335804 |
| `both` | 1 | 1215.043 | 6.832 | 10.629 | 335916 |
| `both` | 2 | 1258.152 | 6.869 | 10.729 | 335952 |
| `both` | 3 | 1341.273 | 6.855 | 10.700 | 335828 |
| `routed` | 1 | 1221.758 | 3.785 | 8.064 | 335964 |
| `routed` | 2 | 1300.393 | 3.938 | 8.334 | 335892 |
| `routed` | 3 | 1217.365 | 3.724 | 7.931 | 335984 |

Every run passed the #429 limits of 30 ms/document mean CPU, 50 ms/document
warm p95 and 524,288 KiB peak RSS. Segmentation with both models increases
CPU time and p95. Routing reduces mean CPU time in all three runs, while
warm p95 remains above the full-document control. Whole-process RSS stays
similar because all variants load both models.

## Offline reproduction

Check out the #445 evaluator revision with the roadmap base in local history.
Use a separate Python 3.12 environment and run the evaluation offline. Follow the
[offline wheel verification and installation](ner-next-experiment.md#evaluation-command-for-the-corpus-custodian)
for both model wheels and the pinned dependencies. Use the same local corpus
file released to the #428 evaluator. Its SHA-256 is
`c61f248db8337632d72682feb9b84ad4ade36eb78010f60b8d28888ccfdd62c6`.
The tool verifies the bytes against that digest before decoding or loading
the models. The frozen `tests/fixtures/ner/dictionary.yaml` has SHA-256
`b22197c7b6733bcacb59bffc1ec7d21db47117d030ce3609b6d0404440647987`.

Set `eval_python` to the evaluation Python executable and `corpus` to the
absolute path of the synthetic corpus. Run from the repository root with an
absolute `eval_python` path. The original evaluator at roadmap base
`33b1ae3c7470fac4feb584e86d8350041b0543bf`
provides the before comparison. Its `metrics.combined` should equal `metrics`
from the new `full` evaluator.

```bash
export PYTHONPATH=src OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
digest=c61f248db8337632d72682feb9b84ad4ade36eb78010f60b8d28888ccfdd62c6
out=$(mktemp -d)
mkdir "$out/base"
git archive 33b1ae3c7470fac4feb584e86d8350041b0543bf | tar -x -C "$out/base"
(cd "$out/base" && "$eval_python" -m tools.ner_bilingual_candidate \
  --corpus "$corpus" --expect-sha256 "$digest") > "$out/before.json"
for variant in full both routed; do
  "$eval_python" -m tools.ner_language_routing \
    --corpus "$corpus" --variant "$variant" > "$out/$variant.json"
done
"$eval_python" - "$out" <<'PYCODE'
import json
import sys
from pathlib import Path

def report(name):
    return json.loads((Path(sys.argv[1]) / (name + '.json')).read_text(encoding='utf-8'))

assert report('before')['metrics']['combined'] == report('full')['metrics']
for variant in ('full', 'both', 'routed'):
    rows = [row for labels in report(variant)['metrics'].values() for row in labels.values()]
    print(variant, {key: sum(row[key] for row in rows) for key in ('tp', 'fp', 'fn')})
PYCODE
```

The comparison prints `full` 82 / 79 / 76, `both` 82 / 80 / 76, and
`routed` 94 / 58 / 64 in TP / FP / FN order. JSON reports contain aggregate
metrics and counts. They do not include input text, entity surfaces, or
individual document or routing decisions.

To reproduce the resource protocol, run each variant three times in a fresh
process, on a core allowed by `taskset`. Run without other benchmark processes
on that core.

```bash
for run in 1 2 3; do
  for variant in full both routed; do
    taskset -c 0 /usr/bin/time -f '%M' -o "$variant-$run.rss" \
      "$eval_python" -m tools.ner_language_routing \
      --corpus "$corpus" --variant "$variant" --timing > "$variant-$run.json"
  done
done
```

## Decision for the next experiment

Freeze the routed-per-sentence variant with the splitter, script counts,
80% rule, both-model fallback, and full-document merge specified above.
Use it as the frozen configuration for the development comparisons before
and after fine-tuning in [#446](https://github.com/MRDK80/privacy-gateway/issues/446).
Retain the historical full-document scores for context. Five of the six
language/type pairs miss at least one quality floor. The routed pair
remains an experimental comparator, with integration contingent on the
fresh blind evaluation and a separate owner decision.
