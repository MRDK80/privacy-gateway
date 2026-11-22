# ADR-446: Fine-tuned bilingual NER candidate

- Status: Accepted for rejection of this candidate, 2026-11-22
- Issue: [#446](https://github.com/MRDK80/privacy-gateway/issues/446)
- Evidence: [training, development and independent blind evaluation](ner-finetune.md)

## Decision

Decline the fine-tuned routed pair for production integration. The trained
pair passed both quality thresholds in five of six rows on development data
and zero of six on the independent synthetic test. On that test the
untrained routed pair found 103 exact entities with 53 false positives.
Fine-tuning yielded 99 exact entities and 78 false positives. Each of the
three trained runs met the CPU, warm latency and peak RSS budgets.

## Consequences

Retain the frozen evidence for future model research. The production contract
requires a qualifying candidate and separate owner approval. Those conditions
gate [#431](https://github.com/MRDK80/privacy-gateway/issues/431) and the
dependent [#432](https://github.com/MRDK80/privacy-gateway/issues/432).
