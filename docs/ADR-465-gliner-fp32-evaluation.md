# ADR-465: Evaluate GLiNER FP32 on v2 and v3

- Status: Proposed
- Task: [#465](https://github.com/MRDK80/privacy-gateway/issues/465)
- Epic: [#428](https://github.com/MRDK80/privacy-gateway/issues/428)

## Result

The tested FP32 configuration met both quality thresholds in four of six groups
on v2 and five of six on v3. Recall passed in every group on both corpora.
Precision fell below 80% for EN PERSON and EN ORG on v2 and EN LOC on v3.
The experiment ended at the quality gate. The MatMul-only and Gather-only
quantization trials and the blind test were not run.

## Method

The experiment used the FP32 and joint MatMul/Gather ONNX INT8 graphs,
tokenizer, adapter, labels, score threshold above 0.5 and text windows pinned
in [ADR-463](ADR-463-gliner-int8-evaluation.md). The same ONNX Runtime CPU
settings and adapter were used for both graphs, substituting only the graph
path. The third configuration reproduced the routed
[#445 spaCy control](ner-language-routing.md) with spaCy 3.8.18 and both
small packages at 3.8.0. The same dictionary detector and resolver were
used across the comparison. Scoring followed the
[#429 exact-boundary and type protocol](ner-baseline.md), with 80 synthetic
documents per corpus and groups assigned by document language.

The SHA-256 values of the FP32 graph, INT8 graph, tokenizer and both corpora
were checked against ADR-463. Inference used ONNX Runtime 1.23.2,
Tokenizers 0.22.2, NumPy 2.5.5 and Python 3.12.3 on one logical CPU.
The control and INT8 results matched ADR-463 in every group.

## Quality by group

Each cell shows TP/FP/FN followed by precision/recall. A dash denotes
undefined precision. The FP32 gate requires precision ≥ 80% and
recall ≥ 75%, checked on integer counts before rounding.

### v2

| Group | #445 | INT8 | FP32 | FP32 gate |
| --- | --- | --- | --- | --- |
| RU PERSON | 23/13/4 · 63.9%/85.2% | 2/2/25 · 50.0%/7.4% | 24/6/3 · 80.0%/88.9% | Pass |
| RU ORG | 14/6/12 · 70.0%/53.8% | 1/2/25 · 33.3%/3.8% | 23/3/3 · 88.5%/88.5% | Pass |
| RU LOC | 20/3/6 · 87.0%/76.9% | 0/0/26 · —/0.0% | 23/4/3 · 85.2%/88.5% | Pass |
| EN PERSON | 15/11/12 · 57.7%/55.6% | 2/2/25 · 50.0%/7.4% | 23/9/4 · 71.9%/85.2% | precision |
| EN ORG | 15/23/11 · 39.5%/57.7% | 1/2/25 · 33.3%/3.8% | 20/7/6 · 74.1%/76.9% | precision |
| EN LOC | 7/2/19 · 77.8%/26.9% | 0/0/26 · —/0.0% | 26/3/0 · 89.7%/100.0% | Pass |

### v3

| Group | #445 | INT8 | FP32 | FP32 gate |
| --- | --- | --- | --- | --- |
| RU PERSON | 23/16/6 · 59.0%/79.3% | 3/2/26 · 60.0%/10.3% | 26/6/3 · 81.2%/89.7% | Pass |
| RU ORG | 15/8/12 · 65.2%/55.6% | 1/0/26 · 100.0%/3.7% | 25/2/2 · 92.6%/92.6% | Pass |
| RU LOC | 20/3/7 · 87.0%/74.1% | 0/0/27 · —/0.0% | 25/3/2 · 89.3%/92.6% | Pass |
| EN PERSON | 22/12/9 · 64.7%/71.0% | 2/2/29 · 50.0%/6.5% | 27/5/4 · 84.4%/87.1% | Pass |
| EN ORG | 14/11/13 · 56.0%/51.9% | 1/0/26 · 100.0%/3.7% | 23/1/4 · 95.8%/85.2% | Pass |
| EN LOC | 9/3/15 · 75.0%/37.5% | 0/0/24 · —/0.0% | 23/6/1 · 79.3%/95.8% | precision |

## Changes in findings

| Corpus | Compared with | Added TP | Lost TP | Added FP | Removed FP |
| --- | --- | ---: | ---: | ---: | ---: |
| v2 | #445 | 53 | 8 | 23 | 49 |
| v2 | INT8 | 133 | 0 | 24 | 0 |
| v3 | #445 | 53 | 7 | 18 | 48 |
| v3 | INT8 | 142 | 0 | 19 | 0 |

FP32 produced 184 raw proposals on v2 and 187 on v3. Of those, 148 and
159 matched exact gold spans. After merging, 133 and 143 correct proposals
were newly present relative to the fixed detector. The merge dropped 11
and 10 exact gold proposals. Baseline conflicts accounted for 4 and 2,
and conflicts with other model proposals for 7 and 8. INT8 produced zero
raw proposals on v2 and one on v3. This diagnostic measured the joint
effect of the existing quantized graph and leaves the contribution of
individual operators undetermined.

## Reproduction

From the local #465 evaluation directory run `bash run.sh > results.json`
with the pinned #463 model files, adapter, disclosed corpus files and
installed environments. The aggregate report includes per-group paired
changes and the exact gate results. The diagnostic script SHA-256 is
`4d6c2fc81e0dbcc30747a1c0d5c6c3c7ba3d2d16b87043a58838c20ebc39834e`.
The repeated aggregate report was byte-identical with SHA-256
`07ce8901d7caa94e6e72b1b9a38bdcd8a37075bd339bc6def5b4c850ef747575`.
