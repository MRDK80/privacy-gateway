# ADR-249: ограниченный мандат epic-runner

Дата: 2026-09-27. Задача: #249. Epic: #248.

## Статус

Принято как контракт будущего epic-runner. Эта задача не реализует GitHub
writes и не запускает новый режим.

## Контекст

Coordinator epic #232 требует отдельного решения человека для каждого push,
PR, merge и изменения issue. Это безопасный режим по умолчанию, но он не
позволяет заранее делегировать прохождение очереди задач одного выбранного
epic. Текст issue, PR, branch head, model output и checkpoint остаются
недоверенными и не могут выдать такое делегирование сами себе.

Нужен второй, явно включаемый режим, который разрешает только заранее
перечисленные действия и не переносит разрешение на другой repository, epic,
SHA или срок. Положительный controller verdict по-прежнему не заменяет local
gate, GitHub CI или независимую проверку следующего side effect.

## Решение

### Версионированная schema и migration policy

Полный lifecycle впервые представлен schema `2.0`. Мандат содержит точные
поля `issued_at`, `expires_at`, `owner_identity`, `limits`, `revoked` и
`approval`; время задаётся целым Unix timestamp в UTC. `limits` связывает
`max_duration_seconds`, `max_task_iterations` и `max_follow_up_issues`, а
`approval` — `mandate_digest`, `approved_by` и `approved_at`.

Schema `1.0` не содержит достаточной authority для безопасной миграции и
поэтому отклоняется с `MANDATE_SCHEMA_UNSUPPORTED`. Автоматически дополнять её
значениями по умолчанию запрещено: владелец должен выпустить и отдельно
утвердить новый мандат `2.0`, получив новый digest.

Непосредственно перед каждым разрешённым side effect runtime получает только
локальный проверенный lifecycle context: текущее время, identity активного
владельца, время старта сессии и фактически использованные task/follow-up
лимиты. Он останавливается с отдельным стабильным machine code при неверной
schema/форме, несовпадении owner или approver, времени до выдачи/после expiry,
отзыве либо превышении duration/task/follow-up limit. Эти проверки выполняются
повторно после обычной identity revalidation и до записи intent в ledger.

### Два режима approval

Без валидного мандата действует прежний per-action режим ADR-233: каждое
внешнее действие требует отдельного решения владельца. Delegated режим
включается только локальным версионированным мандатом, который владелец
утверждает отдельно от issue и task head. Мандат является человеческим
разрешением ровно на перечисленные операции, а не отменой approval boundary.

Мандат хранится вне worktree в закрытом локальном state. Его каноническое
представление и digest связывают:

- `schema_version`, repository identity, номер epic и единственную разрешённую
  `roadmap/<epic>-*` ветку;
- trusted policy SHA, время выдачи, `expires_at`, максимальную длительность и
  число task-итераций;
- allowlist операций, лимит новых follow-up issues и запрет по умолчанию для
  всего отсутствующего;
- identity владельца/approval record, digest всего содержимого и состояние
  отзыва.

Изменение любого связанного поля создаёт новый digest и требует нового
одобрения. Истечение срока, отзыв, несовпадение repository/epic/roadmap/policy
SHA, неизвестное поле или невозможность проверить источник дают
`NEEDS_DECISION` до следующего side effect. Отзыв запрещает новые действия,
но не откатывает уже подтверждённые внешние результаты.

### Разрешённые и запрещённые действия

Allowlist мандата может включать только: создание локальной task-ветки;
commit и push разрешённой task-ветки; создание task PR только в закреплённую
roadmap-ветку; merge этого PR после exact gates; закрытие именно этой task
после post-merge gates; обновление выбранного epic; создание и структурную
привязку ограниченных follow-up issues по утверждённой классификации; создание
итогового roadmap PR в `main` и его merge только после отдельного финального
gate и подтверждённой владельцем consumer demo.

Даже при мандате запрещены force push, удаление `main` или roadmap-ветки,
изменение PR base на иной target, tags, releases, PyPI, платежи и покупка/reset
квоты, repository settings, protection/visibility, secrets/key material,
расширение scope, новый trust source и новые архитектурные либо security
решения. Executor не получает Git/GitHub write; controller остаётся
независимым read-only. Writes выполняет только отдельный trusted delivery
driver после непосредственной revalidation.

### State machine и gates

```text
PLAN -> RUN_TASK -> PR_CI -> MERGE -> POST_MERGE -> DEMO
  -> TASK_DONE -> NEXT_TASK -> FINAL_GATE -> ROADMAP_PR -> MAIN_POST_MERGE
```

Каждый переход закрепляет repository, issue/PR identity, base/head refs и
актуальные SHA. `PR_CI -> MERGE` возможен только после полного local gate,
положительного независимого controller verdict и всех пяти exact PR checks
`SUCCESS` на текущем task head SHA. `MERGE -> POST_MERGE` сначала сверяет
неизменившиеся PR identity и merge outcome. `POST_MERGE -> DEMO` требует пяти
exact checks `SUCCESS` уже на новом roadmap merge SHA; PR CI не переносится.

Task с изменённым наблюдаемым поведением достигает `TASK_DONE` только после
готовой consumer demo и требуемого epic подтверждения владельца. Если текущая
task является только документационной и не меняет наблюдаемое поведение,
assessment явно фиксирует `DEMO_NOT_APPLICABLE` с основанием. Закрытие task и
обновление epic выполняются только после `TASK_DONE` gates и повторной сверки
их identity.

После каждой task очередь пересчитывается из проверенных структурных
parent/sub-issue и dependency facts. Финал epic требует отсутствия открытых
обязательных tasks/follow-ups, полного gate совокупного roadmap SHA,
подтверждённой владельцем итоговой demo, зелёного roadmap PR CI, merge в
`main` и отдельного зелёного post-merge CI нового `main` SHA.

### Неопределённый outcome, stale evidence и resume

Перед каждым write delivery driver сверяет мандат, срок/отзыв, operation
allowlist, identity, base/head и SHA, gates и ledger. Stale SHA возвращает фазу
к соответствующей read-only проверке и запрещает write. Неготовая demo не
может быть отмечена как выполненная и останавливает переход в `TASK_DONE` или
финальный merge.

До внешнего вызова ledger записывает намерение и idempotency identity, после
проверенного результата — completion. Timeout, crash или неоднозначный ответ
после потенциального GitHub write дают `ESCALATE_UNKNOWN_OUTCOME`: операция не
повторяется, пока read-only reconciliation не докажет `APPLIED` или
`NOT_APPLIED`. Непроверяемый результат требует решения владельца.

## Последствия

- Одно одобрение может обслужить только один выбранный epic и ограниченный
  набор заранее известных действий; оно не является глобальным `--yolo`.
- Исходный per-action процесс сохраняется как fail-closed default и fallback.
- Реализация формата мандата, delivery driver, очереди и loop относится к
  следующим задачам #250–#257 и должна закрепить этот контракт тестами.

## Не входит

Runtime epic-runner, GitHub write, изменение публичных Library/`pgw` API,
существующих JSON schemas/machine codes/exit codes, запуск production Codex,
изменение workflows, version bump, release и публикация пакета.
