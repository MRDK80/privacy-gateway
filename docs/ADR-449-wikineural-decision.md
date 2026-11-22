# ADR-449: Decline WikiNEuRal ONNX INT8

- Status: Accepted on 2026-11-22
- Task: [#449](https://github.com/MRDK80/privacy-gateway/issues/449)
- Epic: [#428](https://github.com/MRDK80/privacy-gateway/issues/428)

## Decision

The owner declined WikiNEuRal ONNX INT8 for integration. The candidate missed
the required per-group quality thresholds on both disclosed synthetic corpora.
The distribution gate also remains unmet. Stop this candidate evaluation
before the blind test. [#431](https://github.com/MRDK80/privacy-gateway/issues/431)
and [#432](https://github.com/MRDK80/privacy-gateway/issues/432) require a
qualifying candidate and an owner-approved production contract.

## Evidence

The local diagnostic used the [#429 scoring protocol](ner-baseline.md) and
[#445 routed spaCy control](ner-language-routing.md) at roadmap revision
`fc546f0f052f3e35d7d2de4d4d43ccecb7bba4d5`. Each disclosed corpus had
80 documents. The table shows TP / FP / FN after merging with the baseline
detector. A group passes at precision ≥ 0.80 and recall ≥ 0.75.

| Corpus | Routed control | ONNX FP32 | Repaired ONNX INT8 | INT8 groups passed |
| --- | ---: | ---: | ---: | --- |
| v2 | 94 / 58 / 64 | 121 / 45 / 37 | 121 / 45 / 37 | 0 of 6 |
| v3 | 103 / 53 / 62 | 126 / 43 / 39 | 126 / 44 / 39 | 2 of 6, RU and EN PERSON |

| Corpus | RU PERSON | RU ORG | RU LOC | EN PERSON | EN ORG | EN LOC |
| --- | --- | --- | --- | --- | --- | --- |
| v2 | .733 / .815 | .727 / .615 | .647 / .846 | .778 / .778 | .708 / .654 | .793 / .885 |
| v3 | .806 / .862 | .824 / .519 | .564 / .815 | .879 / .935 | .696 / .593 | .741 / .833 |

The second table gives precision / recall with rounded display values.
Exact-boundary ORG → LOC errors occurred seven times on v2 and eleven
times on v3 in the INT8 combined results. The corresponding FP32 counts
were eight and twelve.

The evaluation used publisher safetensors from
[Babelscape revision bed6ee7](https://huggingface.co/Babelscape/wikineural-multilingual-ner/tree/bed6ee7a45d2827b6c90a4fd7983f0241ae0a5c1),
PyTorch CPU 2.8.0, opset 17 and ONNX Runtime 1.23.2. The initial
per-tensor full-range conversion produced no model proposals. A public
smoke example isolated the loss to MatMul quantization. The repaired
graph used per-channel reduced-range dynamic MatMul quantization and
quantized Gather embeddings. Its SHA-256 was
`9e36ced98e4e048fb75f31d2d1f9a38a3adb7a8ca705c37dc6aac6b5d91aabc5`.

| Corpus | INT8 load (ms) | Mean CPU (ms/doc) | Warm p95 (ms) | Peak RSS (KiB) |
| --- | ---: | ---: | ---: | ---: |
| v2 | 403.815 | 25.497 | 34.574 | 358892 |
| v3 | 411.720 | 26.883 | 36.071 | 358904 |

Each row timed 1,600 document calls after warm-up on one logical CPU of an
AMD Ryzen 5 5600. Another benchmark ran on a separate physical core during
these diagnostics. FP32 exceeded all three limits on both corpora. Formal
three-run acceptance measurements and blind quality results are absent.

Babelscape declares CC BY-NC-SA 4.0. Its noncommercial restriction continues
to apply to use of the model after a technical format conversion. The
diagnostic used local synthetic data. The public license does not grant
the commercial usage rights needed for regular distribution of this
candidate with Privacy Gateway. See the
[license text](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.en)
and the [license review in #449](https://github.com/MRDK80/privacy-gateway/issues/449#issuecomment-7074914743).
