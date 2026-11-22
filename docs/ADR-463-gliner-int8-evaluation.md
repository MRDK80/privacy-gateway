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

## Initial ONNX result

The initial evaluation stopped at the resource gate.
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

## ORT loading and CPU follow-up

The follow-up used the same INT8 graph, tokenization, labels, threshold,
windows, baseline detector and merge. ONNX Runtime 1.23.2 serialized the
optimized graph into a fixed ORT artifact on the target CPU with
`ORT_ENABLE_ALL`. The artifact is 333,668,056 bytes. Its SHA-256 is
`ac473b84cb251bafbc126b0526d6bde6ba4cfa9bcce57ad32b844233f1564b60`.
ORT sessions loaded the artifact by path with further graph optimization
disabled. The diagnostic varied CPU allocation, prepacking and the options
for direct use of ORT bytes and initializers. This build did not expose the
file-backed ORT mapping option. The direct-option rows record the observed
effect of the settings when loading by path.

Each diagnostic row used a fresh process with one warm-up pass and four
timed passes over the same 18-document fixture. Peak RSS at load and current
RSS after warm-up are distinct measurements. These 72-call samples served
to select a configuration for the full protocol.

| Configuration | Peak RSS at load (KiB) | RSS after warm-up (KiB) | Mean CPU (ms/doc) | Warm p95 (ms) |
| --- | ---: | ---: | ---: | ---: |
| onnx | 1038292 | 853284 | 36.69 | 44.972 |
| ort | 1185316 | 748752 | 37.906 | 45.285 |
| ort-direct | 1157572 | 757064 | 36.952 | 45.207 |
| ort-no-pack | 1194792 | 739888 | 44.304 | 53.989 |
| ort-direct-no-pack | 1202576 | 739792 | 44.145 | 52.209 |
| ort-no-arena | 1135952 | 639576 | 36.667 | 44.837 |

In the 72-call ORT sample with the CPU arena disabled, the model call
accounted for 36.336 of 36.667 ms/doc, or about 99% of process CPU time.
Input preparation took 0.145 ms/doc, baseline detection 0.092 ms/doc,
decoding 0.064 ms/doc and merging 0.006 ms/doc. A separate instrumented
ORT trace attributed 50.2% of recorded node-event duration to
DynamicQuantizeMatMul, 14.5% to MatMulIntegerToFloat and 13.7% to
FusedMatMul. Trace timings are diagnostic and excluded from the full runs.

The ORT configuration with the CPU arena disabled had the lowest
post-warm-up RSS in the short ORT measurements. It retained prepacking.
Proposal sets and merged outputs matched the initial ONNX configuration
on 20 separate synthetic inputs, including mixed-language and long text
and an empty input. The comparison contained one model proposal in total.
The matching output digest was
`d9a401c26916f7acecf71df150a90f3ffcda95d4b3565c992531b9f4cbe5c481`.
This was a conversion parity check with limited positive coverage.

### Full resource runs

The selected ORT configuration used the same CPU, synthetic fixture,
warm-up and 100 timed passes as the initial protocol. Profiling was off
during the full runs.

| Trial | Timed calls | Mean CPU (ms/doc), max 30 | Warm p95 (ms), max 50 | Peak RSS (KiB), max 524,288 | RSS after warm-up (KiB) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 1,800 | 36.758 | 44.884 | 1,130,692 | 641,064 |
| 2 | 1,800 | 36.562 | 44.720 | 1,131,276 | 644,160 |
| 3 | 1,800 | 36.909 | 45.103 | 1,130,032 | 639,916 |

Both the CPU and peak RSS limits failed in all three runs. Warm p95 met
the limit. ORT reduced steady memory in the short comparison from about
833 MiB to 625 MiB, while its peak exceeded the original ONNX peak.
The resource gate stopped the follow-up. The v2/v3 comparison and the
blind evaluation remain unrun.

For scale, all six recorded ONNX and ORT runs would fit an illustrative
budget of 40 ms mean CPU, 1.25 GiB peak RSS and the existing 50 ms warm
p95. That would raise the CPU allowance by one third and peak memory by
2.5 times. The example describes this fixture on the reference CPU.
The alternative budget and the decision about this candidate remain open
for separate consideration. The existing #429 thresholds still apply.

With the original experiment artifacts and environment, run
`bash reproduce.sh` from the ORT follow-up directory to regenerate the
ORT model, profile and run the benchmark. Run `bash compare.sh` to
inspect the saved ONNX and ORT trials against both sets of numbers.
The measured harness `measure.py` has SHA-256
`9fbfdda20d382ef623eed3bbfc9e341850eb03039374ef04b9903f2f3e3c1247`.
