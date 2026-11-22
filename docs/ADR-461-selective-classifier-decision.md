# ADR-461: Decline the evaluated selective NER classifier

- Status: Accepted
- Task: [#461](https://github.com/MRDK80/privacy-gateway/issues/461)
- Epic: [#428](https://github.com/MRDK80/privacy-gateway/issues/428)

## Decision

The owner declined the evaluated selective classifier for production.
It reproduced the [#445 control](ner-language-routing.md) exactly on both
disclosed synthetic corpora. Tasks #431 and #432 require a qualifying
candidate and an approved production contract.

## Evaluation

The experiment held the baseline detector, routed small spaCy models,
span boundaries and overlap resolver fixed. A boundary could be revised
when proposals disagreed between LOC and ORG or an original FAC label
followed a location preposition. The resolver preserved the control's
final PERSON spans and all findings outside the selected boundaries.

For the upper bound, the diagnostic enumerated keep, LOC, ORG and drop
for each selected boundary. It counted improvements that preserved all
correct control findings and introduced no new false positives. This
uses the gold labels, as in [ADR-457](ADR-457-ner-reachability.md), within
the narrower set of eligible proposals. The classifier trial used the
frozen #459 model and its validation-selected 0.45 threshold.
Scoring followed the [#429 exact-span protocol](ner-baseline.md).

| Corpus | Selected boundaries | Control EN LOC TP / FP / FN | Upper bound EN LOC TP / FP / FN | Upper bound EN LOC recall |
| --- | ---: | --- | --- | ---: |
| v2 | 2 | 7 / 2 / 19 | 9 / 2 / 17 | 34.6% |
| v3 | 3 | 9 / 3 / 15 | 11 / 3 / 13 | 45.8% |

Both corpora included two FAC proposals that the upper bound recovered
as correct EN LOC. The additional selected boundary on v3 had competing
LOC and ORG proposals. The upper bound removed no false ORG findings
and left the other five groups identical to control. EN LOC recall
remained below the 75% threshold on both corpora.

The classifier produced the same final output as control in all six
groups on each corpus. Control totals were 94 / 58 / 64 on v2 and
103 / 53 / 62 on v3 (TP / FP / FN). A repeat run matched the original
diagnostic output. The experiment used the disclosed corpora exclusively.

The evaluated classifier is the artifact recorded in
[ADR-459](ADR-459-sentence-classifier-decision.md), SHA-256
`def593388124f1575d42e626913004392b23db1243d0d57cf6f9bdd72c3f28fa`.
