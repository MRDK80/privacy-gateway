# ADR-257: synthetic E2E и контролируемый production pilot epic-runner

Дата: 2026-09-28. Задача: #257. Epic: #248.

## Решение

Финальная проверка разделена на воспроизводимый synthetic E2E и отдельный
production pilot. Synthetic E2E проводит две последовательные tasks, добавляет
структурный blocking follow-up текущего epic и nonblocking follow-up другого
epic, проверяет rate-limit pause/resume, crash reconciliation, failed CI и
bounded reuse проверенного lesson. Synthetic fixtures не являются production
proof и не содержат реальных credentials, payload или GitHub writes.

Production pilot выполняется владельцем по закрытой последовательности из
`docs/epic-runner-pilot.md`. Он закрепляет mandate digest, repository, epic,
roadmap SHA, Codex/Python/OS/Bubblewrap versions и primary GitHub check runs.
Linux production adapter обязан использовать Bubblewrap и fail closed, если он
недоступен. Pilot не выполняет merge вне мандата и не публикует raw prompts,
логи, реальные input или model output.

## Критерий результата

Полный local gate и exact CI остаются обязательными. `PAUSED_RATE_LIMIT`,
`NEEDS_DECISION`, revoke, crash и неизвестный outcome имеют явные operator
actions; ни один из них не превращается в автоматическое расширение scope или
повтор внешнего write. Финальная demo владельца и roadmap delivery в `main`
остаются отдельными gates ADR-256.
