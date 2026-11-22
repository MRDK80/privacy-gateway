# ADR-452: Decline the FAC-to-LOC mapping

- Status: Accepted
- Task: [#452](https://github.com/MRDK80/privacy-gateway/issues/452)
- Epic: [#428](https://github.com/MRDK80/privacy-gateway/issues/428)

## Decision

The owner declined the English spaCy FAC-to-LOC mapping for production.
The comparison used the union configuration from [ADR-451](ADR-451-ner-ensemble-decision.md)
and the two disclosed synthetic corpora. The mapping improved English
location recall, which remained below the 75% threshold on both corpora.

## Diagnosis

Among the 28 English locations with no exact typed proposal before overlap
resolution, 22 had exact boundaries with another scored label, four had an
exact proposal only under an excluded label, and two had no overlapping
proposal. Running all four models on every segment recovered no exact
proposal among these 28. Overlap resolution removed three additional exact
English location proposals per corpus.

Across both corpora, the union had 23 English and 20 Russian organization
misses. Respectively, wrong type alone explained seven and eight, boundary
alone explained four and six, overlap resolution suppressed six and two,
and routing accounted for one and two. The other misses involved combined
boundary and type errors, excluded labels, or absent proposals. Wrong type
at exact boundaries accounted for 26 of 41 English organization false
positives and five of 13 Russian organization false positives.

## Disclosed-corpus comparison

Counts are TP / FP / FN for English locations under the [exact-span protocol](ner-baseline.md).

| Corpus | Control union | FAC mapped to LOC | Candidate precision | Candidate recall |
| --- | --- | --- | --- | --- |
| v2 | 9 / 4 / 17 | 12 / 4 / 14 | 75.0% | 46.2% |
| v3 | 7 / 4 / 17 | 9 / 4 / 15 | 69.2% | 37.5% |

The mapping added three and two correct location predictions. It replaced
one and two incorrect location predictions with the same number of new
incorrect predictions. It also removed one incorrect English organization
prediction on v3. PERSON predictions matched the control.

The quality shortfall ended this experiment at the disclosed-corpus stage.
Formal resource acceptance and a fresh blind evaluation were not run.
Tasks #431 and #432 still require a qualifying candidate and an approved
production contract.
