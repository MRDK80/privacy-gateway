# ADR-455: Decline the evaluated context classifier

- Status: Accepted
- Task: [#455](https://github.com/MRDK80/privacy-gateway/issues/455)
- Epic: [#428](https://github.com/MRDK80/privacy-gateway/issues/428)

## Decision

The owner declined the evaluated context classifier for production.
The disclosed-corpus results fell below the quality thresholds. The
experiment ended before formal resource acceptance or a blind test.
Tasks #431 and #432 require a qualifying candidate and an approved
production contract.

## Boundary coverage

The [sentence router and small spaCy models from #445](ner-language-routing.md)
reproduced the control counts of 94 / 58 / 64 on v2 and 103 / 53 / 62 on
v3 (TP / FP / FN). Both disclosed synthetic corpora had 80 documents.

Coverage counts distinct gold mentions with exact proposed boundaries
under any original model label before overlap resolution. Combined
coverage also counts exact typed matches from the fixed baseline detector.

| Corpus | Group | Small pair | Combined | Required for 75% |
| --- | --- | --- | --- | ---: |
| v2 | RU LOC | 26/26 | 26/26 | 20 |
| v2 | RU ORG | 19/26 | 20/26 | 20 |
| v2 | EN LOC | 24/26 | 24/26 | 20 |
| v2 | EN ORG | 21/26 | 21/26 | 20 |
| v3 | RU LOC | 25/27 | 25/27 | 21 |
| v3 | RU ORG | 20/27 | 21/27 | 21 |
| v3 | EN LOC | 22/24 | 22/24 | 18 |
| v3 | EN ORG | 20/27 | 21/27 | 21 |

## Disclosed-corpus evaluation

A multinomial naive Bayes classifier was fitted on 30 synthetic context
templates with the candidate name masked. It used up to five words on each
side of the span. It retained the original mapping when fewer than two
known features were available or the leading score ratio was below 3.
The experiment used the same candidate boundaries and overlap policy as
the control. The baseline detector retained priority.

| Corpus | Group | Control TP / FP / FN | Candidate TP / FP / FN |
| --- | --- | --- | --- |
| v2 | RU PERSON | 23 / 13 / 4 | 23 / 13 / 4 |
| v2 | RU ORG | 14 / 6 / 12 | 14 / 6 / 12 |
| v2 | RU LOC | 20 / 3 / 6 | 20 / 4 / 6 |
| v2 | EN PERSON | 15 / 11 / 12 | 15 / 11 / 12 |
| v2 | EN ORG | 15 / 23 / 11 | 15 / 23 / 11 |
| v2 | EN LOC | 7 / 2 / 19 | 8 / 3 / 18 |
| v3 | RU PERSON | 23 / 16 / 6 | 23 / 16 / 6 |
| v3 | RU ORG | 15 / 8 / 12 | 14 / 7 / 13 |
| v3 | RU LOC | 20 / 3 / 7 | 21 / 4 / 6 |
| v3 | EN PERSON | 22 / 12 / 9 | 22 / 12 / 9 |
| v3 | EN ORG | 14 / 11 / 13 | 14 / 11 / 13 |
| v3 | EN LOC | 9 / 3 / 15 | 9 / 3 / 15 |

Under the [exact-span protocol](ner-baseline.md), English LOC recall
reached 30.8% on v2 and 37.5% on v3 against the 75% threshold.
On v2 the candidate added one correct and two incorrect predictions.
On v3 it added one correct prediction, lost one correct prediction, and
replaced one incorrect prediction with another. PERSON predictions matched
the control. The decision applies to this classifier and training sample.
