# ADR-463: Evaluate GLiNER multi-v2.1 ONNX INT8

- Status: Proposed
- Task: [#463](https://github.com/MRDK80/privacy-gateway/issues/463)
- Epic: [#428](https://github.com/MRDK80/privacy-gateway/issues/428)

## Context

[ADR-461](ADR-461-selective-classifier-decision.md) established that the
selected ambiguous boundaries from #445 could add at most two true EN LOC
findings per disclosed corpus. #463 evaluates a model that proposes its own
boundaries and types. GLiNER replaces the learned component of #445. The
baseline detector and its overlap precedence remain in the measurement.

## Distribution review

| Component | Pinned evidence | Distribution conditions |
| --- | --- | --- |
| GLiNER multi-v2.1 weights | [443d26d654e0324125a96bebd8e796c14ff2efe6](https://huggingface.co/urchade/gliner_multi-v2.1/tree/443d26d654e0324125a96bebd8e796c14ff2efe6) | Apache-2.0 as declared by the model publisher |
| mDeBERTa encoder and tokenizer lineage | [microsoft/mdeberta-v3-base](https://huggingface.co/microsoft/mdeberta-v3-base/tree/a0484667b22365f84929a935b5e50a51f71f159d) and [Microsoft license](https://github.com/microsoft/DeBERTa/blob/master/LICENSE) | MIT. Preserve the Microsoft copyright and permission notice |
| FP32 ONNX and packaged tokenizer | [roselabs-io export at 0a5bb4c09501deeedfb99b32851247e6e36186ab](https://huggingface.co/roselabs-io/gliner-multi-v2.1-onnx/tree/0a5bb4c09501deeedfb99b32851247e6e36186ab) | Publisher names source revision `443d26d6` and identifies the ONNX conversion, includes Apache-2.0 LICENSE and the encoder's MIT text in NOTICE |
| Evaluated INT8 graph | Locally derived from that FP32 export | Preserve LICENSE and NOTICE and identify export and quantization as modifications |

The published license chain permits commercial distribution of the model
artifacts subject to those conditions. The tokenizer is the file distributed
with the pinned export. The reviewed chain relies on that publisher's
provenance declaration. No model artifact is added to the repository.

| Evaluation input | SHA-256 |
| --- | --- |
| FP32 graph | `03f8a927c1c010c337bce2dfe9d25cdb41a245f4a8e26ff8cd457028beb0e3ef` |
| Tokenizer JSON | `c5b8041501fcdee792b9b112dd592861f04391a24633a73fd9f05aaaf6e8eff1` |
| #429 public synthetic held-out split | `c78bf661684acc3195c94fa67a6253de14a9b6663b5e413f0509e1947038b7be` |
| INT8 graph | `3c8c9e57f52528bda7ae13cdc5f0223263237676fd02d5f9d3b11a466f047656` |
| Export LICENSE | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| Export NOTICE | `f4f42326610afce5a87573d56be56d7d38808cc45a6f7920402d965e0969ac8c` |

## Resource evaluation

ONNX Runtime 1.23.2 quantized constant-weight MatMul and Gather with
per-channel, reduced-range signed INT8. ONNX 1.19.0 validated the graph
after restoring a topological order that accounts for references to parent
values inside If subgraphs. This repair changed node order only.
The final graph was 354,286,656 bytes. The experiment used Python 3.12.3,
NumPy 2.5.5 and Tokenizers 0.22.2.

The fixed inference settings were single-document CPU execution, a single
intra-op and inter-op thread, sequential execution, prompts `person`,
`organization`, and `location` for both languages, and a score above 0.5.
The adapter split text into words and punctuation, retained original
character offsets, and bounded the encoder input to 384 words and 512
subtokens including the prompt. Long inputs used windows overlapping by
up to 11 words. The detector applied the existing resolver to model spans
after the baseline findings.

The [#429 resource protocol](ner-baseline.md) used the 18-document public
synthetic fixture at roadmap SHA
`4508caa71d04a4b16a88e05e3d712b38f50f559f`.
Three fresh processes were pinned to logical CPU 4 on an AMD Ryzen 5 5600
under Linux 7.0.0-43-generic x86-64. Each had one untimed pass and 100 timed
passes in fixture order. Peak RSS includes initialization. Timing covers
tokenization, inference, baseline detection and merge.

| Trial | Timed calls | Mean CPU (ms/doc), max 30 | Warm p95 (ms), max 50 | Peak RSS (KiB), max 524,288 |
| --- | ---: | ---: | ---: | ---: |
| 1 | 1,800 | 37.500 | 45.602 | 1,037,524 |
| 2 | 1,800 | 36.526 | 44.801 | 1,037,388 |
| 3 | 1,800 | 36.522 | 44.834 | 1,037,480 |

## Proposed outcome

End evaluation of this pinned INT8 configuration at the resource gate.
All three runs exceeded the mean CPU and peak RSS limits. Warm p95 met the
limit in each run. The peak process footprint was approximately 1,013 MiB.
The four-example smoke run established that the graph returned finite span
logits with the expected shape. It provided no quality or FP32 parity metric.
The quality comparison against #445 on v2 and v3 and the blind test were
skipped. The conclusion applies to the measured artifact and runtime
configuration.

To reproduce locally with the pinned artifact and dependencies in the
evaluation workspace, run `bash run.sh` from the evaluation directory.
It verifies the input hashes, prepares INT8 if needed, runs the smoke test
and launches three fresh benchmark processes. Run `bash demo.sh` to compare
the saved timings with the #429 limits. The local measurement scripts have
these SHA-256 values:

| Script | SHA-256 |
| --- | --- |
| `benchmark.py` | `dec80baa0c9dbd6d6bae146535b678833557a5c789576747e74ee254f4c59dea` |
| `quantize.py` | `41c8032c38ddae2d4c0778c41d2fb7942f293fe565c733f11e0a60acece5bf88` |
| `sort_graph.py` | `dd7ce2800e2241df60815bd3783cb3f85205cf3ba7b11a2c2e044295d7ff68bf` |
