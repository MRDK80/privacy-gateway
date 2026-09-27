# ADR-238: resume, private memory и trust boundaries coordinator

Дата: 2026-09-27. Задача: #238. Epic: #232.

## Статус

Принято для локального checkpoint coordinator и безопасного resume.

## Контекст

Executor workflow уже хранит собственное ограниченное состояние, но оно не
доказывает identity плана coordinator и не фиксирует его подготовительные
side effects. После прерывания нельзя выводить approval или повторять создание
ветки из текста issue, логов, model output либо task head.

## Решение

`tools/agent_coordinator_resume.py` хранит закрытый checkpoint версии `1.0`
в private state directory вне repository. Checkpoint содержит только identity
epic/task, base/head refs и SHA, handover digest, policy provenance, plan
approval, budgets, allowlist, bounded repair counter, redacted status и ledger
завершённых действий. Raw prompts, issue/PR text, comments, logs, model output,
secrets и PII в checkpoint не принимаются.

Resume получает заново проверенные live facts и ожидаемый plan identity.
Продолжение разрешено только при полном совпадении repository, issues,
refs/SHA, digest, policy, approval, allowlist и budgets. Изменение любого поля,
неизвестный ключ, dirty worktree, исчерпанный repair budget или trust-policy
failure дают `BLOCKED`/`ESCALATE`; coordinator не исправляет это сменой base,
расширением прав или новым approval.

Ledger использует фиксированный allowlist локальных подготовительных действий.
Перед вызовом записывается `pending_action`, после успешного возврата —
`completed_actions`. Уже завершённое действие возвращает `NO_OP`; неизвестный
после crash исход даёт эскалацию и автоматически не повторяется. Запись
checkpoint атомарна; это не общая транзакция Git/state и не обещание crash
durability.

Для контекста выбираются только валидные redacted retrospective записи
существующей schema для точных epic/task. Выход дополнительно ограничен
фиксированными полями и числом записей. `AGENTS.md`, role policy и публичный
репозиторий из memory автоматически не меняются.

## Последствия

- Interrupted coordinator run либо безопасно продолжается, либо требует нового
  человеческого решения.
- Недоверенный свободный текст не становится authority и не сохраняется в
  checkpoint.
- Ошибка private storage или превышение repair limit приводит к явной
  эскалации без следующего side effect.

## Не входит

Изменение публичного CLI/Library API, schemas executor/memory, автоматический
merge, GitHub writes, публикация raw retrospective, обучение модели и
автоматическая правка policy.
