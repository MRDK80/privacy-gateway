# ADR-252: trusted delivery одной task по exact gates

Дата: 2026-09-27. Задача: #252. Epic: #248.

## Статус

Принято для Git/GitHub side effects одной task в ограниченном epic-runner.

## Решение

`tools/agent_epic_delivery.py` является отдельной от executor и controller
границей записи. На один вызов он принимает ровно одну операцию, утверждённый
digest epic-мандата, закреплённые repository/epic/task/PR, base/head refs и
SHA. Текст issue, PR и check names считаются недоверенными и не могут добавить
операцию в allowlist мандата.

Для merge требуется успешный read-only assessment ADR-237 фазы `pr` с пятью
точными `SUCCESS` checks текущего head SHA. `pending`, `neutral`, `skipped`,
missing, failed, дубликаты и stale identity дают fail-closed результат до
side effect. Непосредственно перед каждой записью вызывается отдельная live
revalidation; её отрицательный результат блокирует операцию. Обход branch
protection и fallback на более широкий token не выполняются.

Закрытие task и обновление epic требуют отдельного успешного assessment фазы
`post-merge` на закреплённом новом roadmap merge SHA. Ledger дополнительно не
разрешает `update_epic`, пока для той же task/PR/merge SHA не записан receipt
успешного `close_task`. Поэтому PR CI не переносится на post-merge и порядок
`merge -> post-merge CI -> close task -> update epic` сохраняется явно.

До вызова effect приватный ledger вне repository атомарно записывает intent,
после проверенного результата — receipt. Timeout, transport error, пустой
receipt и иной неоднозначный результат становятся
`ESCALATE_UNKNOWN_OUTCOME`. Повтор той же operation identity запрещён, пока
read-only reconciliation не докажет `APPLIED` или `NOT_APPLIED`. Явный отказ
GitHub permission даёт `GITHUB_PERMISSION_DENIED`, а не повтор с иными
полномочиями.

## Границы

Driver предоставляет проверяемую границу для узких adapters commit, push,
создания task PR, merge, закрытия task и обновления epic. Конкретный transport
передаётся как effect после revalidation и не получает права менять request.
Executor и controller по-прежнему не имеют Git/GitHub write.

Не входят runtime epic loop, consumer demo, roadmap PR в `main`, release,
изменение публичных Library/CLI/JSON contracts и выдача credentials.
