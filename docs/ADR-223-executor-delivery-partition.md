# ADR-223: Executor delivery-criteria partition

## Status

Accepted.

## Context

ADR-211 separates patch review from external delivery gates for the controller.
The pinned contract keeps the complete, verbatim acceptance criteria and the
operator-selected `delivery_criterion_indices`; the controller receives derived
`review_criteria` and `pending_delivery_criteria`.

After ADR-221 made the pinned task contract authoritative for the executor, the
executor still received an instruction to implement every acceptance criterion.
That is internally inconsistent when delivery criteria require human-owned PR,
CI or post-merge actions while the executor has no Git or GitHub permissions.
The repeated #182 pilot on #118 demonstrated the failure mode: the repository
gate passed, but fresh executor sessions produced no useful patch and controller
review exhausted the bounded repair loop.

## Decision

When `delivery_criterion_indices` is non-empty, the orchestrator derives the
same ordered partition for the executor that it derives for the controller:

- `review_criteria` contains only repository-result requirements;
- `pending_delivery_criteria` contains the external human-owned gates.

The complete `acceptance_criteria` and their indices remain unchanged in the
pinned contract. The adapter recomputes the expected partition from that
contract and rejects missing, reordered, duplicated, all-delivery or otherwise
inconsistent input before invoking Codex.

The generated prompt puts the derived partition in a separate
`pinned-patch-requirements` envelope. It tells the executor to implement only
`review_criteria` and to leave `pending_delivery_criteria` pending. Runtime
evidence and repair feedback are excluded from that envelope and cannot change
the classification.

Contracts without delivery gates retain their prior behavior: the executor
implements the full acceptance criteria. An absent or empty delivery-index list
must not create a derived partition.

## Security and compatibility

The partition narrows the executor's requested work; it does not expand allowed
paths, tools, permissions, sandbox, policy authority or repair budget. The
existing OS boundary and post-execution scope checks remain authoritative.

This is an internal prompt/request correction. Public CLI and Library APIs,
canonical JSON schemas, machine codes, process exit codes, token formats and
stored evidence formats are unchanged. A successful repeated live pilot is
still required before #182 can be closed.
