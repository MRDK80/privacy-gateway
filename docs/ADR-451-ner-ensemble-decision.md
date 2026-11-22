# ADR-451: Decline the four-model NER ensemble

- Status: Accepted
- Task: [#451](https://github.com/MRDK80/privacy-gateway/issues/451)
- Epic: [#428](https://github.com/MRDK80/privacy-gateway/issues/428)

## Decision

The owner declined both configurations evaluated in #451. The next
investigation, [#452](https://github.com/MRDK80/privacy-gateway/issues/452),
will diagnose missed locations and organization errors before selecting a
remedy. A qualifying candidate and an approved production contract are
required before #431 and #432.

## Evidence

The experiment used spaCy 3.8.18 and the four Russian and English small and
medium language packages at version 3.8.0. The [#445 router](ner-language-routing.md)
selected sources by sentence. For each routed language, selection accepted
PERSON and ORG from the medium model and LOC from the small model. Union
accepted proposals from both sizes. Both configurations preserved the
baseline detector's priority and used the same overlap resolver.

Each disclosed synthetic corpus contained 80 documents. Counts are
TP / FP / FN under the [#429 exact-span protocol](ner-baseline.md).

| Corpus | #445 control | Language and type selection | Union | Groups passing, selection / union |
| --- | --- | --- | --- | --- |
| v2 | 94 / 58 / 64 | 100 / 50 / 58 | 98 / 65 / 60 | 1/6 and 1/6 |
| v3 | 103 / 53 / 62 | 100 / 55 / 65 | 107 / 62 / 58 | 0/6 and 0/6 |

Even before overlap resolution, the baseline and all routed sources
proposed the exact English LOC span and type for only 12 of 26 gold
mentions on v2 and 10 of 24 on v3. Both figures are below the 75% recall
floor. On v3, removing the Russian medium model from union removed six
correct and four incorrect predictions and restored one correct and five
incorrect predictions. The same source contributed one correct and one
incorrect prediction on v2.

The diagnostic process loaded all four models and peaked at 869,220 KiB
RSS. The 512 MiB budget applies to the complete detector. The diagnostic
included multiple evaluation modes. Formal three-run resource acceptance
and a fresh blind test were skipped after the disclosed quality failure.

## Model sourcing

The standalone SlovNet NER and Navec archives contained no license notice
for their contents. Their source repositories publish MIT licenses, and
redistribution rights for the specific archives were left unresolved.
The experiment substituted Explosion's official
[Russian medium package](https://github.com/explosion/spacy-models/releases/tag/ru_core_news_md-3.8.0).
It and the [English medium package](https://github.com/explosion/spacy-models/releases/tag/en_core_web_md-3.8.0)
include MIT and source notices. The English source notices identify
OntoNotes as commercially licensed by Explosion. The final distribution
and its obligations were outside this local diagnostic.

| Evaluated wheel | SHA-256 |
| --- | --- |
| ru_core_news_md 3.8.0 | `43f456d40e4b70726874e01ce01fce32f50089ba98a2660292e9a6c63a06ebe9` |
| en_core_web_md 3.8.0 | `5e6329fe3fecedb1d1a02c3ea2172ee0fede6cea6e4aefb6a02d832dba78a310` |
