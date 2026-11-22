# ADR-430: Local NER evaluation for Russian and English

- Status: Accepted for the rejection of `xx_ent_wiki_sm` 3.8.0 only
- Issue: [#430](https://github.com/MRDK80/privacy-gateway/issues/430)
- Epic: [#428](https://github.com/MRDK80/privacy-gateway/issues/428)
- Evaluation protocol: [#429 synthetic NER baseline](ner-baseline.md)

## Accepted decision

The owner rejected spaCy `xx_ent_wiki_sm` 3.8.0 for integration. The combined
detector passed the resource limits on the reference host and met both quality
thresholds only for LOC in Russian and English. The #429 thresholds stay fixed.

At the owner's request, [the next experiment](ner-next-experiment.md) prepares
the evaluation of a different candidate. No new model has been qualified.
The prospective production contract below remains unapproved. Model selection
and production implementation require separately accepted decisions.

## Candidate and frozen evaluation rule

The evaluated artifact was Explosion's CPU model `xx_ent_wiki_sm` 3.8.0 with
spaCy 3.8.18. The model wheel declares MIT. Its packaged metadata credits
WikiNER, licensed CC BY 4.0, to Joel Nothman, Nicky Ringland, Will Radford,
Tara Murphy and James R. Curran. The evaluated wheel SHA-256 matched the official release.

`6f3c4b853852ea9e9d2dc76cc950dddb10a7e4c42d813308caefe6c5e8be2f0a`

[Model release](https://github.com/explosion/spacy-models/releases/tag/xx_ent_wiki_sm-3.8.0) ·
[WikiNER source](https://figshare.com/articles/Learning_multilingual_named_entity_recognition_from_Wikipedia/5462500)

The evaluator and its rule were fixed before running held-out. Send the full
original document through one multilingual `xx` NER pipeline with no language
routing, language oracle, translation, split by script or confidence filter.
Run the current detector with the #429 dictionary and preserve all resulting
spans, including regex and secret findings. Map model `PER` to `PERSON`,
`ORG` to `ORG`, and `LOC` to `LOC`. Ignore `MISC`. Add a mapped model span
only when it shares no character with a baseline span. Adjacent spans do
not conflict. This gives exact duplicates and all type or boundary conflicts
to the baseline. spaCy's entity list is non-overlapping. The evaluation
passes only document text to the detector and uses the #429 document language
for scoring. The candidate emits span metadata in memory and the evaluator
prints only aggregate metrics and timings.

## Quality

Each row shows baseline and combined-detector counts in TP / FP / FN order.
Precision and recall shown for the combined detector are rounded here.
Thresholds use the unrounded ratios. Held-out requires precision ≥ 0.80 and
recall ≥ 0.75 for every row. Only the held-out rows determine acceptance.

| Split | Language | Type | Baseline counts | Combined counts | Combined precision | Combined recall | Held-out outcome |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| Tuning | RU | PERSON | 1 / 1 / 3 | 3 / 2 / 1 | 0.600 | 0.750 | — |
| Tuning | RU | ORG | 1 / 1 / 2 | 1 / 1 / 2 | 0.500 | 0.333 | — |
| Tuning | RU | LOC | 0 / 0 / 4 | 4 / 1 / 0 | 0.800 | 1.000 | — |
| Tuning | EN | PERSON | 1 / 1 / 3 | 4 / 3 / 0 | 0.571 | 1.000 | — |
| Tuning | EN | ORG | 1 / 1 / 3 | 1 / 1 / 3 | 0.500 | 0.250 | — |
| Tuning | EN | LOC | 0 / 0 / 3 | 2 / 1 / 1 | 0.667 | 0.667 | — |
| Held-out | RU | PERSON | 1 / 3 / 5 | 3 / 5 / 3 | 0.375 | 0.500 | Fail both |
| Held-out | RU | ORG | 1 / 1 / 3 | 2 / 1 / 2 | 0.667 | 0.500 | Fail both |
| Held-out | RU | LOC | 0 / 0 / 4 | 4 / 0 / 0 | 1.000 | 1.000 | Pass |
| Held-out | EN | PERSON | 1 / 3 / 5 | 4 / 4 / 2 | 0.500 | 0.667 | Fail both |
| Held-out | EN | ORG | 1 / 1 / 3 | 3 / 1 / 1 | 0.750 | 0.750 | Fail precision |
| Held-out | EN | LOC | 0 / 0 / 4 | 4 / 0 / 0 | 1.000 | 1.000 | Pass |

The held-out split has four gold LOC occurrences per language, four ORG and
six PERSON. Exact duplicates and spelling variants still count by their actual
offsets. The result applies to short synthetic documents and the evaluated
merge rule. It gives no evidence of coverage for personal data in arbitrary
documents or of successful end-to-end preparation.

## Resources

Measured on AMD Ryzen 5 5600, Linux 7.0.0-43-generic x86-64, Python 3.12.3,
NumPy 2.5.5, Thinc 8.3.14 and Blis 1.3.4. Three fresh CPU-only processes were
pinned to logical CPU 0. Each loaded config and model, made one untimed
pass over the 18 held-out documents, then timed 1,800 in-process calls.
Cold model load includes the spaCy import, model version lookup and pipeline
construction. Cold first call measures the first combined detection after
loading. Peak RSS covers the complete process. The cold measurements are
observations without a #429 threshold, and a fresh process does not flush
the OS file cache.

| Run | Cold model load (ms) | First call (ms) | Mean CPU (ms/doc) | Warm p95 (ms/doc) | Peak RSS (KiB) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 1521.226 | 1.919 | 1.288 | 1.462 | 502900 |
| 2 | 1514.867 | 1.933 | 1.279 | 1.446 | 503072 |
| 3 | 1489.988 | 1.962 | 1.305 | 1.485 | 503028 |
| #429 limit | — | — | 30 | 50 | 524288 |

All three runs passed all resource limits. Peak RSS left roughly 21 MiB of
headroom on this host. Cold load and resource measurements cover a standalone
evaluator. A production application's resident memory, model lifecycle and
long-document behavior need separate verification.

## Offline reproduction

On Linux x86-64 with Python 3.12, provision a trusted local wheelhouse before
going offline. Include the pinned model wheel, spaCy and their transitive
wheels, as well as the three project runtime dependencies and their transitive
wheels. Use the matching NumPy, Thinc and Blis versions above for a comparable
run. The reference run used cryptography 50.0.2, keyring 25.7.0 and PyYAML 6.0.4.
Use a fresh evaluation venv, separate from the project development environment.
The evaluated source/corpus base was `79c8ed950240b93e00bdc6577d12fdbbc7d03871`
with the evaluator added by #430. Use `sha256sum` and compare the wheel to
the publisher's checksum above before allowing installation.

```bash
wheelhouse=/path/to/verified-wheelhouse
model_wheel="$wheelhouse/xx_ent_wiki_sm-3.8.0-py3-none-any.whl"
sha256sum "$model_wheel"
python3.12 -m venv /tmp/pgw-ner-eval
eval_python=/tmp/pgw-ner-eval/bin/python
"$eval_python" -m pip install --no-index --find-links "$wheelhouse" \
  spacy==3.8.18 numpy==2.5.5 thinc==8.3.14 blis==1.3.4 \
  cryptography==50.0.2 keyring==25.7.0 PyYAML==6.0.4 "$model_wheel"
"$eval_python" -m pip check

export PYTHONPATH=src OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
"$eval_python" -m tools.ner_baseline --split tuning
"$eval_python" -m tools.ner_baseline --split held-out
"$eval_python" -m tools.ner_candidate --split tuning
for run in 1 2 3; do
  taskset -c 0 /usr/bin/time -f 'peak_rss_kib=%M' \
    "$eval_python" -m tools.ner_candidate --split held-out --timing
done
```

Run the evaluation commands from the repository root. Use an allowed logical
CPU ID in place of `0` if needed. The evaluator loads the installed model
locally and requests CPU execution. It neither downloads models nor uses
keys. Reproduction measures detector output, not the proposed preparation
contract below.

## Unapproved production proposal for a qualifying candidate

- **Enablement.** A future accepted implementation uses one explicit opt-in in
  the local detector YAML shared by CLI and Library preparation. An omitted or
  disabled setting keeps current behavior and avoids loading the optional
  runtime. Provision a reviewed runtime and version-pinned model from offline
  artifacts. Never download or install while processing input. Keep plaintext
  and span contents in the existing local trust boundary.
- **Language and labels.** Select the qualified model or model pair at
  configuration time. The accepted decision must specify how RU, EN and mixed
  input are routed and how their offsets are preserved. Require explicit mapping
  into PERSON, ORG and LOC.
  Discard unsupported labels. Test the chosen model and mapping on mixed
  text before acceptance.
- **Precedence.** Keep all resolved baseline findings, including existing
  regex and secret priorities. Deduplicate identical spans and admit only
  non-overlapping valid model spans. Preserve the metadata-only diagnostics
  and current routing policy. In enabled preparation, require an existing
  block or tokenization decision for admitted model entities. A known
  model span dropped for overlap must be fully covered by the spans that will
  be tokenized, or preparation must block before publishing artifacts. Run
  the independent output validator as today. The evaluator keeps the
  baseline span on conflict and measures detection quality, so it does
  not measure these preparation blockers.
- **LOC and versions.** Establish LOC support in routing, tokenization,
  validation and restoration before exposing LOC from an enabled detector.
  Use the established token framing and encrypted manifest mechanism.
  Test new-reader compatibility with pre-LOC artifacts and fail closed
  for unknown or unsupported types. A pre-LOC version is not a supported
  reader for new artifacts containing LOC. Test offline RU and EN
  preparation and restoration with LOC, preserving the original text.
- **Failure and compatibility.** If enabled, missing dependencies, a
  missing or incompatible model, load or inference errors, or invalid model
  offsets must stop detection or preparation. Never silently fall back to
  dictionary-only behavior. A failed preparation yields no READY, protected
  output or new publication. Errors expose only sanitized diagnostics
  within existing CLI and Library error paths. Preserve the disabled-mode
  behavior, public signatures, CLI schema, machine/exit codes and output
  contracts in the [Library API](LIBRARY_API.md), [ADR-150](ADR-150-cli-json-contract.md)
  and [ADR-151](ADR-151-cli-introspection.md). Any public contract change
  requires a separate decision. The [dependency contract](DEPENDENCIES.md)
  and [security boundary](SECURITY.md) apply to both modes.
