# Privacy pipeline benchmark protocol

This protocol defines a paired comparison between two Privacy Gateway revisions
using project-owned synthetic data. The benchmark runs locally, through the
[public Library API](LIBRARY_API.md), with a deterministic local stand-in for
the provider. It measures the registered cases under the
[project threat model](threat-model.md).

## Preregister the comparison

Before either revision runs, freeze a registration in a versioned local
snapshot and record its SHA-256 digest. Include:

- Full baseline and candidate commit SHAs, supported Python version, operating
  system, resolved dependencies and public API configuration.
- The fixture generator, fixture inventory and ground truth, local provider
  stand-in, scoring script and their snapshot digests.
- The exact commands, fresh workspace and keyring setup, seed, case counts,
  metrics, thresholds, exclusions and report schema defined below.

Each revision gets its own disposable workspace and local test keyring. Keep
the registration, inputs, artifacts, keys and restoration outputs local. Only
aggregate counts and the overall decision belong in a shared report. Use no
real PII, secrets, hostnames, connection strings, external corpora or external
scoring services.

## Freeze the cases and order

Create 192 scored case pairs with stable ASCII IDs. Each pair gives both
revisions the same synthetic input and expected behavior. Give all generated
sensitive occurrences unique fixture values. Record their exact Unicode code
point offsets, exact text and expected behavior before either revision runs.

| Set | Pairs | Ground truth |
| --- | ---: | --- |
| Authorized round trips | 96 | 24 each for synthetic email, phone, mixed PII and repeated use of one PII class with distinct fixture values. Within each group, 12 provider responses retain every token and 12 omit the even-numbered protected occurrences (numbered from one). |
| Secret blocks | 24 | One supported synthetic blocking secret per input. Preparation raises the documented error and yields no protected payload. |
| Invalid restoration | 24 | Eight unknown-token responses, eight malformed-token responses and eight integrity failures on disposable test artifacts. Strict restore returns the registered public error class and no restored text. |
| Placebo | 48 | Benign text without annotated sensitive spans. Preparation preserves the text and produces zero tokens. The provider echoes it and restore returns it unchanged. |

For the round-trip cases, register fixed, unique benign anchors around each sensitive
occurrence. Give every round-trip case at least two sensitive occurrences and
keep the slots disjoint. The stand-in builds the response from the protected text and its
registered anchors. Retained slots carry the opaque protected segment from the
current output. Omitted slots are removed entirely. Precompute the expected
restored text from the original fixture and the same slot-selection plan.
For invalid restoration, register the exact mutation for each case. Expect
`StrictTokenError` for unknown and malformed tokens and `IntegrityError` for
tampered protected artifacts. Use a disposable copy for each tamper case. Keep the original fixture and failure-case artifacts local.

Use the public UTF-8 seed `pgw-privacy-benchmark-v1`. Derive fixture material
from SHA-256 of the UTF-8 string `seed:fixture:case-id:index`, using decimal
indices starting at zero. Freeze field formatters and collision resolution in
the generator. Sort pairs by the bytes of SHA-256 of `seed:order:case-id`,
breaking ties by case ID. Use the low bit of the first digest byte of
`blind-seed:execute:case-id` for within-pair execution order. These seeds govern
fixtures and ordering. Gateway cryptographic randomness comes from the normal
local runtime.

## Blind the scoring

A coordinator runs both revisions and assigns opaque observation IDs. The
scorer receives pairs with two observations, case IDs and ground truth. Set the
order independently with the low bit of the first SHA-256 byte of
`blind-seed:report:case-id`. The coordinator samples a separate 256-bit
blinding seed, commits its SHA-256 digest in the registration and retains the
seed with the observation mapping. Preserve both locally for audit and replay. Strip revision, variant, execution order, timing,
diagnostics and filesystem paths from scoring inputs. Keep the observation
mapping and execution log with the coordinator until the score output is frozen.

Add two unscored positive-control pairs under ordinary opaque IDs. Pair a
known correct observation with one containing a deliberately leaked synthetic
value in protected text, and pair another correct observation with one whose
restoration has a deliberately changed character. Require the frozen scorer
to identify the failing member and the intended metric in both pairs. Reveal
control IDs only after scoring. A control failure invalidates the comparison.

## Score once and apply the thresholds

Score by integer counts, per revision and fixture set. Score observable public
behavior. Detection coverage for PII is inferred from protected slots and exact
restoration, since the public API exposes protected output and counts.

| Metric | Numerator and denominator | Candidate threshold |
| --- | --- | --- |
| PII detection coverage | Annotated occurrences replaced by a token in their registered slot / all annotated round-trip occurrences | 100% |
| Blocking-secret detection | Registered secret cases with the expected preparation error and no payload / 24 | 24 of 24 |
| Tokenization integrity | Round-trip cases with every annotated value absent from protected text, exact expected token count and unchanged benign anchors and text / 96 | 96 of 96 |
| Placebo false positives | Placebo cases with a preparation error, nonzero token count or any changed protected text / 48 | 0 of 48 |
| Authorized restoration | Round trips with exact expected restored text and exact restored and missing token counts / 96 | 96 of 96 |
| Placebo restoration | Placebo cases restored byte-for-byte with zero restored and missing tokens / 48 | 48 of 48 |
| Invalid restoration | Invalid cases with the registered public error class and no returned restored text / 24 | 24 of 24 |

Report the baseline and candidate counts, paired pass-to-fail and fail-to-pass
counts per metric, and the number of missing or invalid observations. Accept a
candidate only when every absolute threshold passes and no scored case regresses
on any metric. Compare coverage regressions per annotated occurrence and all
other regressions per case. A benchmark pass justifies a claim about this frozen fixture set.

The frozen scoring script validates snapshot digests and case completeness,
consumes all blinded pairs in one pass, and writes a canonical score file. The
coordinator freezes its digest before revealing labels and controls. Use the
same script's registered report stage to join scores to revision labels and
render the final aggregates. Unexpected application errors, timeouts and
missing observations caused by a revision count as failures against the fixed
denominators. An independently recorded infrastructure failure outside both
revisions invalidates the whole batch. Record the cause and rerun both
revisions under the identical registration. Fixture and scoring defects require
a new registration and a full rerun. Preserve superseded score digests and
record why they were superseded. Manual exclusions, rescoring and threshold
changes within a registered comparison are prohibited.

The implementation of the generator, harness and scorer belongs to the follow-up
after protocol approval.
