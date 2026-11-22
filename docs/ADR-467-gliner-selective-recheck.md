# ADR-467: Evaluate selective rechecking for GLiNER FP32

- Status: Proposed
- Task: [#467](https://github.com/MRDK80/privacy-gateway/issues/467)
- Epic: [#428](https://github.com/MRDK80/privacy-gateway/issues/428)

## Quality result

Selective rechecking met precision ≥ 80% and recall ≥ 75% in all six
groups on each disclosed corpus. It reduced false positives by 14 on v2
and 11 on v3. True positives increased by one on v2 and fell by three
on v3. These measurements characterize the frozen rule on v2 and v3.
The blind test was not run.

## Frozen rule

Use the pinned GLiNER FP32 graph, tokenizer and adapter from
[ADR-463](ADR-463-gliner-int8-evaluation.md). Preserve the existing
three prompts, windows, score threshold above 0.5 and character offsets.
Use each proposal's maximum logit across overlapping windows. A proposal
with logit above 0 and at most 1.5 is eligible for checking in the
containing sentence. Run the [#445 routed small spaCy pair](ner-language-routing.md)
only on sentences containing eligible proposals, once per sentence.
Keep a checked proposal when #445 agrees on exact boundaries and type.
Preserve the other FP32 proposals, including spans without a containing
sentence. Apply the existing resolver with the dictionary detector's
priority. #445 contributes confirmation only.

Calibration used 72 new labeled synthetic documents from 12 template
families, six per document language, with 12 mixed documents. The
selection minimized FP+FN over logits 0, 0.25, 0.5, 0.75, 1, 1.5 and 2,
breaking ties by fewer FN, fewer reviews and the lower cutoff. Logit 0
served as the no-op control. At 1.5 (sigmoid score about 0.818), the
calibration aggregate was 211/4/5 TP/FP/FN versus 198/21/18 for FP32.
The configuration was frozen before reading v2/v3. The calibration
contains no exact document duplicates from either corpus.

## Disclosed comparison

Each corpus contains 80 documents. It uses the pinned hashes from
ADR-463 and [#429 exact-boundary and type scoring](ner-baseline.md),
grouped by document language. The #445 and FP32 group metrics matched
[#465](https://github.com/MRDK80/privacy-gateway/pull/466) exactly.
Each cell shows TP/FP/FN and precision/recall. Gates were evaluated
on integers before rounding.

### v2

| Group | #445 | GLiNER FP32 | Recheck |
| --- | --- | --- | --- |
| RU PERSON | 23/13/4 · 63.9%/85.2% | 24/6/3 · 80.0%/88.9% | 24/6/3 · 80.0%/88.9% |
| RU ORG | 14/6/12 · 70.0%/53.8% | 23/3/3 · 88.5%/88.5% | 21/2/5 · 91.3%/80.8% |
| RU LOC | 20/3/6 · 87.0%/76.9% | 23/4/3 · 85.2%/88.5% | 25/1/1 · 96.2%/96.2% |
| EN PERSON | 15/11/12 · 57.7%/55.6% | 23/9/4 · 71.9%/85.2% | 23/4/4 · 85.2%/85.2% |
| EN ORG | 15/23/11 · 39.5%/57.7% | 20/7/6 · 74.1%/76.9% | 22/4/4 · 84.6%/84.6% |
| EN LOC | 7/2/19 · 77.8%/26.9% | 26/3/0 · 89.7%/100.0% | 25/1/1 · 96.2%/96.2% |

### v3

| Group | #445 | GLiNER FP32 | Recheck |
| --- | --- | --- | --- |
| RU PERSON | 23/16/6 · 59.0%/79.3% | 26/6/3 · 81.2%/89.7% | 26/5/3 · 83.9%/89.7% |
| RU ORG | 15/8/12 · 65.2%/55.6% | 25/2/2 · 92.6%/92.6% | 21/0/6 · 100.0%/77.8% |
| RU LOC | 20/3/7 · 87.0%/74.1% | 25/3/2 · 89.3%/92.6% | 26/1/1 · 96.3%/96.3% |
| EN PERSON | 22/12/9 · 64.7%/71.0% | 27/5/4 · 84.4%/87.1% | 28/3/3 · 90.3%/90.3% |
| EN ORG | 14/11/13 · 56.0%/51.9% | 23/1/4 · 95.8%/85.2% | 22/1/5 · 95.7%/81.5% |
| EN LOC | 9/3/15 · 75.0%/37.5% | 23/6/1 · 79.3%/95.8% | 23/2/1 · 92.0%/95.8% |

### Changes relative to FP32

| Corpus | Reviewed documents | Reviewed proposals | Routed sentences | Added / lost TP | Added / removed FP |
| --- | ---: | ---: | ---: | ---: | ---: |
| v2 | 23 | 34 | 25 | 4 / 3 | 0 / 14 |
| v3 | 26 | 36 | 29 | 4 / 7 | 1 / 12 |

The raw filter removed 29 proposals on v2 and 33 on v3, including 7 and
14 exact gold proposals before the resolver. Remaining exact gold
proposals blocked by the dictionary baseline or another proposal fell
from 11 to 4 on v2 and from 10 to 2 on v3. The resolver can retain a
previously suppressed proposal after filtering removes its competitor.
RU ORG recall fell to 80.8% on v2 and 77.8% on v3.

## Reproduction

Python 3.12.3, ONNX Runtime 1.23.2, NumPy 2.5.5, Tokenizers 0.22.2
and spaCy 3.8.18 with both small model packages at 3.8.0. From the local
#467 evaluation directory, generate calibration with `python3 make_tuning.py`
and select the rule with `bash run.sh tune`. Use the frozen configuration
and checksum with `bash run.sh quality`. Use `bash run.sh fp32` or
`bash run.sh recheck` for one resource trial in a fresh process.
The runner uses the #463 and #465 local environments and artifacts.

| Input or output | SHA-256 |
| --- | --- |
| FP32 graph | `03f8a927c1c010c337bce2dfe9d25cdb41a245f4a8e26ff8cd457028beb0e3ef` |
| Tokenizer JSON | `c5b8041501fcdee792b9b112dd592861f04391a24633a73fd9f05aaaf6e8eff1` |
| Calibration fixture | `4fda0ab95332e37ea7d0e0199f9fa94d285d92ff6670d2cf8aecffdf166a4276` |
| Frozen configuration | `aa1fc907756bdc751060eb1630ef627ac3e9b2c77b5ff2f0c5b98cc794835e90` |
| Study script | `741932ee50b4f78b5aaf684e429bfd452689de3712e18ea48eb00e92cdfc8712` |
| Quality report and byte-identical repeat | `8474cb4a5cd913d8099284bb97c29139d55caac1c93b80cdc11009e1656993bf` |

## Resource comparison

The [#429 resource protocol](ner-baseline.md) used the fixed 18-document
public synthetic fixture on an AMD Ryzen 5 5600 under Linux x86-64.
Each variant ran in three fresh processes pinned to logical CPU 4.
Each process completed one warm-up pass and 100 timed passes. Mean CPU
covers the full path through the resolver, using process CPU time. p95
uses nearest-rank wall time. Peak RSS includes initialization. In the
recheck process the spaCy pair loaded on first use.

The current budget is 30 ms mean CPU per document, 50 ms warm p95 and
512 MiB peak RSS. The illustrative budget is 40 ms, 50 ms and 1.25 GiB.
Each resource pass requires all three limits in all three trials.

| Variant | Trial | CPU (ms/doc) | p95 (ms) | Peak RSS (KiB) | Documents checked | Spans checked | Routed sentences | Current #429 | Illustrative |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| FP32 | 1 | 62.549 | 76.111 | 2,388,656 | 0 | 0 | 0 | Fail | Fail |
| FP32 | 2 | 62.428 | 75.879 | 2,388,612 | 0 | 0 | 0 | Fail | Fail |
| FP32 | 3 | 62.642 | 76.172 | 2,388,676 | 0 | 0 | 0 | Fail | Fail |
| Recheck | 1 | 62.989 | 78.911 | 2,388,632 | 400 | 600 | 400 | Fail | Fail |
| Recheck | 2 | 62.842 | 78.910 | 2,388,588 | 400 | 600 | 400 | Fail | Fail |
| Recheck | 3 | 62.947 | 78.910 | 2,388,632 | 400 | 600 | 400 | Fail | Fail |

| Trial | Recheck − FP32 CPU (ms/doc) | Recheck − FP32 p95 (ms) | Recheck − FP32 peak RSS (KiB) |
| --- | ---: | ---: | ---: |
| 1 | +0.440 | +2.800 | -24 |
| 2 | +0.414 | +3.031 | -24 |
| 3 | +0.304 | +2.739 | -44 |

| Variant | Budget | CPU excess (ms/doc) | p95 excess (ms) | Peak RSS excess (MiB) |
| --- | --- | ---: | ---: | ---: |
| FP32 | Current | 32.642 | 26.172 | 1820.7 |
| FP32 | Illustrative | 22.642 | 26.172 | 1052.7 |
| Recheck | Current | 32.989 | 28.911 | 1820.6 |
| Recheck | Illustrative | 22.989 | 28.911 | 1052.6 |

In each recheck trial #445 processed 400 sentences in 400 of 1,800
document calls (22.2%). The recheck added 0.304–0.440 ms of mean CPU and
2.739–3.031 ms of p95 across the paired runs. Peak RSS was approximately
2.28 GiB for both variants. All runs exceeded all three limits under
both budgets. The measured quality gain leaves the resource gate unmet.
The current #429 contract applies.
