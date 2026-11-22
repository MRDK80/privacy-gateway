# ADR-457: Record the NER reachability result

- Status: Accepted
- Task: [#457](https://github.com/MRDK80/privacy-gateway/issues/457)
- Epic: [#428](https://github.com/MRDK80/privacy-gateway/issues/428)

## Result

Both disclosed synthetic corpora admit a single output meeting precision
of at least 80% and recall of at least 75% in all six groups under ideal
type assignment and the existing merge rules. This result establishes
reachability on v2 and v3 with access to gold labels. An actual candidate
and an approved production contract are required before #431 and #432.

## Method

Reproduce #445 using the pinned small spaCy pair and the sentence router
at roadmap SHA `559de37986aaf76649d04796f259e5016c5b2820`.
The control totals were 94 / 58 / 64 on v2 and 103 / 53 / 62 on v3
(TP / FP / FN). The scorer verified the corpus digests before processing.

For each document, collect distinct exact boundaries from the routed models
before label filtering. Keep the fixed baseline output. Assign the gold type
to each eligible exact model proposal and discard other proposals. Exact
proposals overlapping the fixed baseline are blocked unless the baseline
already supplies that gold mention. The remaining correct proposals had
no mutual overlaps. The existing resolver accepted them all together.
Scoring uses the [exact-span protocol](ner-baseline.md).

| Corpus | Group | TP / FP / FN | Precision | Recall | Missing exact boundary | Blocked by baseline |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| v2 | RU PERSON | 25 / 2 / 2 | 92.6% | 92.6% | 1 | 1 |
| v2 | RU ORG | 20 / 2 / 6 | 90.9% | 76.9% | 6 | 0 |
| v2 | RU LOC | 26 / 0 / 0 | 100.0% | 100.0% | 0 | 0 |
| v2 | EN PERSON | 21 / 2 / 6 | 91.3% | 77.8% | 5 | 1 |
| v2 | EN ORG | 21 / 2 / 5 | 91.3% | 80.8% | 5 | 0 |
| v2 | EN LOC | 24 / 0 / 2 | 100.0% | 92.3% | 2 | 0 |
| v3 | RU PERSON | 23 / 2 / 6 | 92.0% | 79.3% | 6 | 0 |
| v3 | RU ORG | 21 / 0 / 6 | 100.0% | 77.8% | 6 | 0 |
| v3 | RU LOC | 25 / 0 / 2 | 100.0% | 92.6% | 2 | 0 |
| v3 | EN PERSON | 26 / 2 / 5 | 92.9% | 83.9% | 4 | 1 |
| v3 | EN ORG | 21 / 0 / 6 | 100.0% | 77.8% | 6 | 0 |
| v3 | EN LOC | 22 / 0 / 2 | 100.0% | 91.7% | 2 | 0 |


Of the 42 misses, 39 lacked an exact model boundary and three were blocked
by the baseline. All 12 false positives came from the fixed baseline.
RU ORG on both corpora and EN ORG on v3 each had zero additional TP margin
against the recall threshold. The repeat run matched the original result.
The experiment used the disclosed corpora exclusively.
