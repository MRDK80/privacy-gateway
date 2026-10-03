# ADR-233: контракт локального agent-координатора

Дата: 2026-09-27. Задача: #233. Epic: #232.

## Статус

Принято для документирования первой версии coordinator. Реализация discovery,
branch preparation и delivery assessment относится к последующим задачам epic.

## Контекст

`tools/agent_orchestrate.py` реализует ограниченный цикл
`executor -> gate -> controller` из ADR-165. Историческое имя orchestrator не
означает, что инструмент выбирает epic или task, создаёт ветки либо принимает
delivery-решения. Такие полномочия нельзя неявно добавить существующим ролям.

Issue, PR, comments, logs, model output и task head недоверенны. Одновременно
координатору нужны проверяемые GitHub/Git факты, pinned SHA, одобрение человека
и безопасный handover в существующий цикл.

## Решение

Вводится отдельная локальная роль **coordinator**. Она планирует и готовит
работу, но не заменяет executor, gate, controller или человека. Канонический
контракт роли, состояния, approvals и stop conditions описан в
[`agent-coordinator.md`](agent-coordinator.md).

Первая версия CLI-first и обслуживает один репозиторий. Coordinator может
читать локальные Git-факты и GitHub metadata, составлять план и после явного
одобрения создать только локальную task-ветку от повторно проверенного roadmap
SHA. Любая GitHub write, push, PR, merge, смена base, закрытие issue, а также
архитектурное или security-решение требует отдельного человеческого решения с
повторной проверкой target и SHA непосредственно перед действием.

Coordinator формирует versioned handover с provenance, но не меняет схемы
executor/controller в этой задаче. Адаптация handover к существующему task
contract выполняется только после валидации и не расширяет permissions,
allowlist или budget. Положительный controller verdict остаётся review patch,
а не разрешением delivery; применяется порядок ADR-211.

## Последствия

- Слово orchestrator в существующих именах сохраняется для совместимости, но
  не обозначает автономного coordinator.
- Неоднозначность, stale refs, dirty tree, scope expansion и недоступность
  первичных evidence останавливают workflow без побочных эффектов следующей
  фазы.
- Resume разрешён только при совпадении versioned handover, pinned identities и
  уже подтверждённых evidence; иначе требуется новый plan/approval.
- Автоматический merge, изменение `AGENTS.md` и публикация raw retrospective
  запрещены.

## Не входит

Runtime-код coordinator, discovery и ranking, создание веток, новый JSON
contract, изменение CLI/Library API, существующих adapters, schemas, machine
codes или exit codes.
