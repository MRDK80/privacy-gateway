# ADR-459: Decline the sentence context classifier

- Status: Accepted
- Task: [#459](https://github.com/MRDK80/privacy-gateway/issues/459)
- Epic: [#428](https://github.com/MRDK80/privacy-gateway/issues/428)

## Decision

The owner declined the evaluated classifier for production. It passed both
quality thresholds in zero of six groups on each disclosed synthetic corpus.
The experiment ended at disclosed quality evaluation. Formal resource
measurements and blind testing were outside the resulting decision.
Tasks #431 and #432 require a qualifying candidate and an approved production
contract.

## Evaluation

The deterministic multinomial logistic regression used nearby words and the
original model label. The marked name was excluded from the input features.
Training used 328 annotated proposals from synthetic Russian and English
sentence templates. A separate 53-proposal validation set used different
names and constructions and selected the 0.45 confidence cutoff. The model
and cutoff were frozen before v2/v3 scoring. The evaluation checked that the
synthetic template names did not occur in either corpus. The template-based
training limits the diversity of contexts.

The evaluation reproduced the [#445 control](ner-language-routing.md) on
both corpora. The classifier reused the routed boundaries, fixed baseline
priority and overlap resolution from [ADR-457](ADR-457-ner-reachability.md).
The [#429 protocol](ner-baseline.md) requires precision at least 80% and
recall at least 75%, independently in all six groups.

| Corpus | Group | Control TP / FP / FN | Candidate TP / FP / FN | Candidate precision | Candidate recall |
| --- | --- | --- | --- | ---: | ---: |
| v2 | RU PERSON | 23 / 13 / 4 | 3 / 2 / 24 | 60.0% | 11.1% |
| v2 | RU ORG | 14 / 6 / 12 | 2 / 4 / 24 | 33.3% | 7.7% |
| v2 | RU LOC | 20 / 3 / 6 | 20 / 7 / 6 | 74.1% | 76.9% |
| v2 | EN PERSON | 15 / 11 / 12 | 3 / 4 / 24 | 42.9% | 11.1% |
| v2 | EN ORG | 15 / 23 / 11 | 5 / 10 / 21 | 33.3% | 19.2% |
| v2 | EN LOC | 7 / 2 / 19 | 9 / 2 / 17 | 81.8% | 34.6% |
| v3 | RU PERSON | 23 / 16 / 6 | 5 / 3 / 24 | 62.5% | 17.2% |
| v3 | RU ORG | 15 / 8 / 12 | 4 / 2 / 23 | 66.7% | 14.8% |
| v3 | RU LOC | 20 / 3 / 7 | 20 / 9 / 7 | 69.0% | 74.1% |
| v3 | EN PERSON | 22 / 12 / 9 | 3 / 3 / 28 | 50.0% | 9.7% |
| v3 | EN ORG | 14 / 11 / 13 | 4 / 5 / 23 | 44.4% | 14.8% |
| v3 | EN LOC | 9 / 3 / 15 | 9 / 3 / 15 | 75.0% | 37.5% |

The candidate added two true English LOC predictions on v2. It lost 54
true PERSON/ORG predictions on v2 and 58 on v3 relative to the control.
This decision applies to the evaluated model and training sample.

| Frozen artifact | SHA-256 |
| --- | --- |
| Training proposals | `17efa88ccedb3645a5b4f07cb8a5f202f098031196bea4618292987ef6f30c84` |
| Validation proposals | `61acd5f9c2464f8d8c33c7a9c740311dcac1d0eeda5c3e8c36a8ff26b74d2dd8` |
| Classifier | `def593388124f1575d42e626913004392b23db1243d0d57cf6f9bdd72c3f28fa` |
