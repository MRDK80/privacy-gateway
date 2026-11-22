# Offline fine-tuning protocol for bilingual NER

[#446](https://github.com/MRDK80/privacy-gateway/issues/446) evaluates whether
fine-tuning the small Russian and English models improves exact PERSON, ORG,
and LOC detection. The experiment uses the [#429 protocol](ner-baseline.md)
and the [frozen #445 route](ner-language-routing.md). The 80 disclosed
documents from #445 serve as historical evidence. This study generates fresh
train and development data and reserves a separate independently authored
synthetic blind test.

## Frozen training and development protocol

The generator owns separate name inventories and sentence template families
for train and development. It writes 326 train and 102 dev documents in
deterministic UTF-8 JSONL. The synthetic genres are email, requests, signatures,
short messages and negative examples. Inputs include mixed-language messages,
Russian inflections, repeated names, legal designations and dictionary
collisions. EN LOC/ORG contrast pairs repeat a surface in contexts indicating a
place or organization. The two splits use separate sentence contexts and
invented names. Each split uses four different intentional dictionary probes
listed by the generator. Dictionary-prefix overlaps are intentional and
the legacy detector still wins intersections.

Annotate exact, half-open Unicode code-point spans including inflected
forms and legal designations that are part of an organization name. Exclude
delimiting punctuation and quotes around a simple name. For an organization
written as ООО «name», include the whole designation, including the embedded
quotes. Repeated occurrences get separate annotations. The negative examples
treat a printed string used as an identifier or specimen as non-entity text.
The document language and mixed marker are scoring metadata.

Start from the MIT-licensed `ru_core_news_sm` and `en_core_web_sm` 3.8.0
release wheels, with spaCy 3.8.18. The
[artifact provenance and hashes](ner-next-experiment.md#offline-artifact-checks-before-running)
include the declared source material licenses or terms for Nerus, OntoNotes 5,
ClearNLP and WordNet. The generated supervised examples are project-authored
synthetic text. Use the [offline installation protocol](ner-next-experiment.md#evaluation-command-for-the-corpus-custodian)
for the pinned runtime and wheels. Preserve the native tokenizers and both
`tok2vec` and `ner` pipes. Use the sentence splitter and script router from
#445 to assign training examples. For a both-model route, the same labeled
sentence is shown to both models with their respective native labels. A
training span crossing a sentence boundary or failing token alignment is an
error. The EN training examples use `GPE` for the synthetic place mentions.
Inference still maps `GPE` and `LOC` to LOC.

Resume each pipeline from its released weights with spaCy's
`Language.resume_training()` optimizer. Shuffle the fixed examples in each
of eight epochs with seed 446 for RU and 447 for EN, batch size eight, and
dropout 0.1. Train the two pipelines sequentially on CPU. Compare the full
untrained control, #445 routed untrained pair and #445 routed fine-tuned pair
on dev. Compare the routed variants first by number of rows clearing both
combined-detector quality floors, then by exact micro F1 on all combined
counts. Prefer the untrained pair on an exact tie. This ranks configurations
for the next test, which applies the per-row requirements independently.

| Split | Documents RU / EN | Mixed RU / EN | RU gold PERSON / ORG / LOC | EN gold PERSON / ORG / LOC |
| --- | --- | --- | --- | --- |
| Train | 163 / 163 | 20 / 20 | 161 / 120 / 120 | 140 / 121 / 120 |
| Dev | 51 / 51 | 6 / 6 | 42 / 37 / 42 | 36 / 37 / 42 |

## Development comparison

The cells give exact TP / FP / FN and precision / recall. Quality floors
use unrounded precision ≥ 0.80 and recall ≥ 0.75 independently in each
language and type. All spans in a mixed document are scored in its
surrounding language.

### All 102 dev documents

| Mode | Language | Type | Full | Routed | Fine-tuned routed |
| --- | --- | --- | --- | --- | --- |
| Combined | RU | PERSON | 36 / 31 / 6 · 0.537 / 0.857 | 42 / 5 / 0 · 0.894 / 1.000 | 42 / 1 / 0 · 0.977 / 1.000 |
| Combined | RU | ORG | 30 / 10 / 7 · 0.750 / 0.811 | 30 / 3 / 7 · 0.909 / 0.811 | 36 / 2 / 1 · 0.947 / 0.973 |
| Combined | RU | LOC | 27 / 11 / 15 · 0.711 / 0.643 | 33 / 6 / 9 · 0.846 / 0.786 | 41 / 0 / 1 · 1.000 / 0.976 |
| Combined | EN | PERSON | 23 / 10 / 13 · 0.697 / 0.639 | 32 / 9 / 4 · 0.780 / 0.889 | 32 / 1 / 4 · 0.970 / 0.889 |
| Combined | EN | ORG | 24 / 32 / 13 · 0.429 / 0.649 | 31 / 11 / 6 · 0.738 / 0.838 | 36 / 11 / 1 · 0.766 / 0.973 |
| Combined | EN | LOC | 16 / 0 / 26 · 1.000 / 0.381 | 19 / 0 / 23 · 1.000 / 0.452 | 36 / 0 / 6 · 1.000 / 0.857 |
| Model only | RU | PERSON | 36 / 32 / 6 · 0.529 / 0.857 | 42 / 6 / 0 · 0.875 / 1.000 | 42 / 3 / 0 · 0.933 / 1.000 |
| Model only | RU | ORG | 30 / 9 / 7 · 0.769 / 0.811 | 30 / 1 / 7 · 0.968 / 0.811 | 36 / 0 / 1 · 1.000 / 0.973 |
| Model only | RU | LOC | 27 / 11 / 15 · 0.711 / 0.643 | 33 / 6 / 9 · 0.846 / 0.786 | 41 / 0 / 1 · 1.000 / 0.976 |
| Model only | EN | PERSON | 23 / 10 / 13 · 0.697 / 0.639 | 32 / 9 / 4 · 0.780 / 0.889 | 32 / 1 / 4 · 0.970 / 0.889 |
| Model only | EN | ORG | 25 / 31 / 12 · 0.446 / 0.676 | 32 / 10 / 5 · 0.762 / 0.865 | 37 / 10 / 0 · 0.787 / 1.000 |
| Model only | EN | LOC | 16 / 0 / 26 · 1.000 / 0.381 | 19 / 0 / 23 · 1.000 / 0.452 | 36 / 0 / 6 · 1.000 / 0.857 |

### 12 mixed dev documents

| Mode | Language | Type | Full | Routed | Fine-tuned routed |
| --- | --- | --- | --- | --- | --- |
| Combined | RU | PERSON | 0 / 8 / 0 · 0.000 / — | 0 / 4 / 0 · 0.000 / — | 0 / 0 / 0 · — / — |
| Combined | RU | ORG | 6 / 0 / 0 · 1.000 / 1.000 | 6 / 0 / 0 · 1.000 / 1.000 | 6 / 0 / 0 · 1.000 / 1.000 |
| Combined | RU | LOC | 2 / 0 / 10 · 1.000 / 0.167 | 6 / 0 / 6 · 1.000 / 0.500 | 12 / 0 / 0 · 1.000 / 1.000 |
| Combined | EN | PERSON | 0 / 0 / 0 · — / — | 0 / 0 / 0 · — / — | 0 / 0 / 0 · — / — |
| Combined | EN | ORG | 4 / 2 / 2 · 0.667 / 0.667 | 6 / 0 / 0 · 1.000 / 1.000 | 6 / 0 / 0 · 1.000 / 1.000 |
| Combined | EN | LOC | 7 / 0 / 5 · 1.000 / 0.583 | 9 / 0 / 3 · 1.000 / 0.750 | 12 / 0 / 0 · 1.000 / 1.000 |
| Model only | RU | PERSON | 0 / 8 / 0 · 0.000 / — | 0 / 4 / 0 · 0.000 / — | 0 / 0 / 0 · — / — |
| Model only | RU | ORG | 6 / 0 / 0 · 1.000 / 1.000 | 6 / 0 / 0 · 1.000 / 1.000 | 6 / 0 / 0 · 1.000 / 1.000 |
| Model only | RU | LOC | 2 / 0 / 10 · 1.000 / 0.167 | 6 / 0 / 6 · 1.000 / 0.500 | 12 / 0 / 0 · 1.000 / 1.000 |
| Model only | EN | PERSON | 0 / 0 / 0 · — / — | 0 / 0 / 0 · — / — | 0 / 0 / 0 · — / — |
| Model only | EN | ORG | 4 / 2 / 2 · 0.667 / 0.667 | 6 / 0 / 0 · 1.000 / 1.000 | 6 / 0 / 0 · 1.000 / 1.000 |
| Model only | EN | LOC | 7 / 0 / 5 · 1.000 / 0.583 | 9 / 0 / 3 · 1.000 / 0.750 | 12 / 0 / 0 · 1.000 / 1.000 |

Fine-tuning yields 223 / 15 / 13 combined TP / FP / FN on dev, compared
with 187 / 34 / 49 for the untrained routed pair and 156 / 94 / 80 for
the full control. It clears both floors in five rows, compared with
three for routed and zero for full. The remaining EN ORG row reaches
36/47 precision. The baseline takes precedence over one exact EN ORG
proposal. The model-only run reaches 37/47 precision in this row.
The remaining gains are concentrated in entity-type decisions and
missed spans, especially EN LOC recall (19/42 to 36/42).

| Combined diagnostic | Full | Routed | Fine-tuned routed |
| --- | ---: | ---: | ---: |
| FP with wrong type only | 24 | 23 | 10 |
| FP with wrong boundary only | 23 | 2 | 2 |
| FP with wrong type and boundary | 11 | 0 | 1 |
| FP with no gold overlap | 36 | 9 | 2 |
| Exact gold blocked by preserved baseline | 1 | 1 | 1 |
| Exact gold blocked by model precedence | 20 | 0 | 0 |
| Discarded model proposals | 64 | 3 | 4 |
| Exact FN | 80 | 49 | 13 |

The dev set uses independent invented roots and sentence templates, but shares
some entity morphology and suffix conventions with training. It also contains
only sentence-level script changes in mixed messages. The 12 mixed documents
exercise both models within a document across separate snippets. They provide
limited evidence about mid-sentence language changes. Select the fine-tuned
routed pair for the blind test under the fixed dev rule.

## Offline reproduction of training and development

Use the offline venv described above on Linux x86-64. The reference run used
Python 3.12.3, spaCy 3.8.18, NumPy 2.5.5, Thinc 8.3.14, Blis 1.3.4,
cryptography 50.0.2, keyring 25.7.0, PyYAML 6.0.4, and Unicode 15.0.0
on AMD Ryzen 5 5600. Run from the repository root with an absolute
`eval_python` path.

| Artifact | SHA-256 |
| --- | --- |
| Train JSONL | `b102618a98462ca8e501e21cc362a56a2b1586d79a30581fe181fc65a3782e2c` |
| Dev JSONL | `eef982c6530b40bba83822202b1329107e8d2d9fe1fb28a9d053293b32cbb1f8` |
| Fine-tuned RU model directory | `bbc49ba2c9ea84c25d3977925032a675933de2c5cea3f272143a334b6d5b720f` |
| Fine-tuned EN model directory | `d9156da7e0b3cca9bf604e385a2ef7e39855a8506ee92363e41978ac42258f04` |

The training run sees 304 RU and 305 EN sentence examples per epoch. A model
digest hashes the UTF-8 serialization of the sorted JSON mapping from each
relative POSIX file path to the file's SHA-256, with separators `,` and `:`
and no trailing newline. The evaluator checks the corpus digest before
decoding records and checks both model digests before loading trained weights.
Keep trained models and reports in local files. Product dependencies and the
detect/prepare path are outside this experiment.

```bash
export PYTHONPATH=src PYTHONHASHSEED=0 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
out=$(mktemp -d)
train_sha=b102618a98462ca8e501e21cc362a56a2b1586d79a30581fe181fc65a3782e2c
dev_sha=eef982c6530b40bba83822202b1329107e8d2d9fe1fb28a9d053293b32cbb1f8
ru_sha=bbc49ba2c9ea84c25d3977925032a675933de2c5cea3f272143a334b6d5b720f
en_sha=d9156da7e0b3cca9bf604e385a2ef7e39855a8506ee92363e41978ac42258f04
"$eval_python" -m tools.ner_finetune_corpus --out "$out/corpus"
taskset -c 0 "$eval_python" -m tools.ner_finetune train \
  --corpus "$out/corpus/train.jsonl" --expect-sha256 "$train_sha" \
  --output "$out/models" > "$out/train.json"
for variant in full routed; do
  taskset -c 0 "$eval_python" -m tools.ner_finetune evaluate \
    --corpus "$out/corpus/dev.jsonl" --expect-sha256 "$dev_sha" \
    --configuration "$variant" > "$out/dev-$variant.json"
done
taskset -c 0 "$eval_python" -m tools.ner_finetune evaluate \
  --corpus "$out/corpus/dev.jsonl" --expect-sha256 "$dev_sha" \
  --configuration trained --models "$out/models" \
  --expect-model-sha256 "$ru_sha" "$en_sha" > "$out/dev-trained.json"
"$eval_python" - "$out" <<'PYCODE'
import json
import sys
from pathlib import Path

for variant in ('full', 'routed', 'trained'):
    report = json.loads((Path(sys.argv[1]) / f'dev-{variant}.json').read_text())
    totals = [row for rows in report['metrics'].values() for row in rows.values()]
    print(variant, {key: sum(row[key] for row in totals) for key in ('tp', 'fp', 'fn')})
PYCODE
```

For the blind evaluation, replace the corpus path and digest with the
independent controller's reviewed artifacts. Run the selected trained
configuration three times with `--timing` and measure peak RSS using
`/usr/bin/time -f '%M'` around each fresh process. Use one allowed logical core
and numerical library threads limited to one. Report exact-count quality,
cold pipeline load, 100 timed passes after one untimed pass, mean CPU,
nearest-rank warm p95, and peak RSS for all three processes. An undefined
precision or recall fails its required threshold.

## Independent blind evaluation

The independent controller verified the 80-document corpus digest
`6f7c5fad12a29670df5984008ebab6cfa156d4b3dc65010da79914a43807e591`
and the frozen source manifest digest
`cf6aae09f1fc7ba33154b3f7b53507d1bea1321fe083a4ecedfc4d80c120987b`.
The sample had 40 documents per language, including 10 mixed and five
entity-free documents per language. The controller verified exact offsets and
no normalized document, labeled surface, or masked template overlap between
the train, development, and blind splits. The trainer received neither the
blind documents nor the blind predictions before candidate selection.

The cells show TP / FP / FN followed by precision / recall. The first two
columns are untrained controls. The final column is the frozen fine-tuned
candidate. None of its six combined-detector rows passes both floors.

| Language | Label | Full control | Routed control | Fine-tuned routed |
| --- | --- | --- | --- | --- |
| RU | PERSON | 23 / 34 / 6 · 0.404 / 0.793 | 23 / 16 / 6 · 0.590 / 0.793 | 26 / 24 / 3 · 0.520 / 0.897 |
| RU | ORG | 14 / 13 / 13 · 0.519 / 0.519 | 15 / 8 / 12 · 0.652 / 0.556 | 15 / 9 / 12 · 0.625 / 0.556 |
| RU | LOC | 19 / 2 / 8 · 0.905 / 0.704 | 20 / 3 / 7 · 0.870 / 0.741 | 12 / 7 / 15 · 0.632 / 0.444 |
| EN | PERSON | 18 / 12 / 13 · 0.600 / 0.581 | 22 / 12 / 9 · 0.647 / 0.710 | 20 / 13 / 11 · 0.606 / 0.645 |
| EN | ORG | 11 / 22 / 16 · 0.333 / 0.407 | 14 / 11 / 13 · 0.560 / 0.519 | 16 / 22 / 11 · 0.421 / 0.593 |
| EN | LOC | 9 / 4 / 15 · 0.692 / 0.375 | 9 / 3 / 15 · 0.750 / 0.375 | 10 / 3 / 14 · 0.769 / 0.417 |

The routed control found 103 exact entities with 53 false positives. The
fine-tuned variant found 99 with 78 false positives. Development results
favored the fine-tuned weights (five rows passed both floors) but did not
transfer to the independently written document families. The template-based
training corpus has limited stylistic variety. The result does not establish
which training-data or optimization change would recover the lost precision.

All three processes agreed on every quality count. Each used one logical CPU
on the AMD Ryzen 5 5600 and timed 8,000 calls after one untimed pass. The
offline spaCy 3.8.18 environment ran on Linux x86-64 with Python 3.12.3.
Peak RSS covers the two loaded pipelines and the complete process.

| Run | Cold model load (ms) | Mean CPU (ms/doc) | Warm p95 (ms/doc) | Peak RSS (KiB) |
| --- | ---: | ---: | ---: | ---: |
| 1 | 1181.604 | 4.555 | 7.484 | 314720 |
| 2 | 1255.387 | 4.504 | 7.294 | 314568 |
| 3 | 1249.193 | 4.456 | 7.251 | 314576 |

The independent controller recommends declining this artifact. All runs meet
the #429 resource limits. The blind quality result blocks product integration.

For reproduction, use the trained model digests above, the reviewed corpus
and the offline environment from the development comparison. Set
`blind_corpus` to the custodian's local file and `blind_sha` to the digest
recorded above. Repeat the candidate command three times in fresh processes.

```bash
for run in 1 2 3; do
  taskset -c 0 /usr/bin/time -f '%M' -o "$out/blind-$run.rss" \
    "$eval_python" -m tools.ner_finetune evaluate \
    --corpus "$blind_corpus" --expect-sha256 "$blind_sha" \
    --configuration trained --models "$out/models" \
    --expect-model-sha256 "$ru_sha" "$en_sha" --timing > "$out/blind-$run.json"
done
```
