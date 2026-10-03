# ADR-237: read-only delivery assessment coordinator

Дата: 2026-09-27. Задача: #237. Epic: #232.

## Статус

Принято для первой версии coordinator delivery assessment.

## Контекст

Положительный controller verdict доказывает только review patch. Он не
доказывает полный локальный gate на текущем SHA, направление task PR, GitHub
CI или post-merge CI нового roadmap SHA. Существующий `agent_pr_checks.py`
проверяет checks PR, но не связывает их с полным gate evidence, согласованным
scope и отдельной post-merge фазой.

## Решение

`tools/agent_coordinator_delivery.py` выполняет только read-only assessment.
Вход закрепляет repository, epic/task, PR, roadmap base ref/SHA, task head
ref/SHA, allowed paths и полный `repository-full` gate evidence. Текст PR,
названия checks и ответы GitHub остаются недоверенными данными.

Для фазы `pr` PASS (`TASK READY FOR REVIEW`) требует одновременно:

- task head -> `roadmap/<epic>-*` в том же repository и точные base/head SHA;
- diff без файлов вне allowlist;
- полный успешный gate profile на `snapshot_commit == head_sha` и
  `base_sha == base_sha` assessment;
- ровно найденные обязательные jobs текущей конфигурации: четыре matrix jobs
  `test` (Ubuntu/Windows, Python 3.11/3.12) и `pre-commit`, все `SUCCESS`;
- неизменившуюся PR identity после чтения checks.

Для фазы `post-merge` PASS (`TASK DONE`) дополнительно требует `MERGED` PR,
точный merge commit, roadmap ref на этом commit и те же пять успешных check
runs commit SHA. PR CI не переносится на merge commit.

Пустой, дублированный, missing, queued, pending, skipped, cancelled, failed,
timed-out, neutral либо неизвестный check fail-closed. Изменение base/head/SHA,
diff, merge identity или roadmap ref инвалидирует assessment. Вывод содержит
только компактные facts, metrics локального gate и ссылки check runs; сырые
логи не копируются.

## Последствия и границы

Инструмент не запускает local gate заново и не выполняет commit, push,
создание/редактирование PR, merge, изменение base, комментарии или закрытие
issue. Эти side effects остаются отдельными approval gates. Статический набор
jobs соответствует фактическим workflow задачи #237; изменение CI matrix
требует отдельного обновления контракта и тестов.
