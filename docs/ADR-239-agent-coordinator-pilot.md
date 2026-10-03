# ADR-239: воспроизводимый CLI pilot coordinator

Дата: 2026-09-27. Задача: #239. Epic: #232.

## Статус

Принято для финального интеграционного pilot epic #232.

## Контекст

Задачи #233–#238 независимо зафиксировали контракт, discovery, branch
preflight, handover, delivery assessment и resume. Финальная проверка должна
связать эти части в одну цепочку, но не расширять права coordinator и не
передавать реальные secrets/PII внешней модели.

## Решение

Pilot состоит из двух раздельных контуров.

1. Автоматизированный synthetic E2E создаёт одноразовый Git repository и
   проходит `PLAN_APPROVAL -> branch preparation -> validated handover ->
   TASK READY FOR REVIEW -> post-merge TASK DONE -> idempotent resume` через
   production-функции coordinator. GitHub и check runs представлены закрытым
   synthetic adapter; тест не выполняет network write, push, merge или close.
2. Оператор выполняет read-only live discovery и реальные repository gates по
   инструкции в [agent-coordinator-pilot.md](agent-coordinator-pilot.md).
   Production Codex adapter разрешено запускать только после отдельного
   согласования расхода, точного allowlist и task contract. Dry-run либо
   synthetic adapter не выдаются за реальный Codex-вызов.

Негативные сценарии остаются распределёнными по contract tests исходных
компонентов. Pilot-документ содержит traceability matrix, поэтому отсутствие,
`pending`, `skipped` или stale evidence не может быть потеряно при handover.

## Последствия

- Полная локальная цепочка воспроизводима без внешней модели и реальных данных.
- PR CI и post-merge CI остаются внешними последовательными gates и не
  подменяются synthetic evidence.
- Фактический запуск production Codex adapter — отдельное одобренное действие,
  а не скрытый prerequisite test suite.

## Не входит

Новый публичный CLI, единая команда без approval gates, GitHub writes из
coordinator, автоматический merge/close, version bump, release или изменение
branch protection.
