# ADR-211: patch review и внешние delivery gates в pilot

## Статус

Принято для задачи #211, epic #179. Владелец согласовал разделение после
прогона #182 на #136.

## Контекст

В прогоне #182 полный дословный contract #136 содержал требования к task PR и
exact CI. Executor изменил только разрешённый тестовый файл, профиль
`repository-full` прошёл, но Controller вернул `FAIL_ESCALATE` с причиной
`permissions_change`: разрешения `push` и `create_pr` у ролей равны
`false`. Исправлять patch было нечего, а создавать PR из pilot запрещено.

Прежнее определение `PASS` в `docs/agent-contracts.md` относилось ко всем
критериям задачи. Игнорировать PR/CI без явной границы нельзя: положительный
вердикт тогда выглядел бы как доказательство готовности задачи.

## Решение

- Оператор передаёт **все** acceptance criteria дословно и отдельно закрепляет
  1-based индексы критериев внешних delivery gates. Классификация не выводится
  из текста issue или моделью; пустой список сохраняет прежнее поведение.
- Индексы должны быть уникальны, находиться в пределах исходного списка и не
  покрывать его целиком. Иначе contract отклоняется до вызова роли.
- Executor получает полный contract. Controller получает полный contract,
  исходный список и явное разбиение на `review_criteria` и
  `pending_delivery_criteria`. Adapter сверяет разбиение с закреплёнными
  индексами до вызова Codex. Вход модели не может расширить scope,
  permissions или trust source.
- Положительный ControllerVerdict означает, что **patch review** прошёл по
  `review_criteria`, trusted policy, snapshot и полному deterministic gate.
  Он не утверждает выполнение `pending_delivery_criteria`. Controller обязан
  указать ожидание внешних gates в `notes`.
- При pending delivery gates оркестратор никогда не выдаёт `RunResult PASS`
  или `PASS_WITH_NOTES`: положительный verdict записывается в приватную
  retrospective, а публичный результат остаётся `FAIL_ESCALATE` с новым
  machine code `EXTERNAL_GATE_PENDING` и process exit code 20. Это
  non-repairable handoff человеку, не доказательство `TASK DONE`.
- PR, exact CI, merge и post-merge CI проверяются вне pilot в порядке
  `зелёный PR CI → merge → зелёный post-merge CI → закрытие задачи →
  обновление epic`. Никакие Git/GitHub permissions ролей не меняются.
- Ключ записи прогона учитывает весь закреплённый contract: terminal state
  другого текста, scope, permissions или разбиения нельзя ошибочно
  переиспользовать. Без индексов старый ключ сохраняется.

Канонические role schemas и форма публичного `RunResult` не меняются.
Меняются семантика положительного verdict при явном разбиении и перечень
machine codes; это ограниченное, документированное изменение контракта.

## Fail-closed границы

Нельзя относить к внешним gates весь список критериев. Неверное или
несогласованное разбиение отклоняется до вызова Controller. Полнота gate,
scope, snapshot, pinned policy, protected paths и проверка verdict не
ослабляются. Внешние gates не считаются успешными без первичных результатов
GitHub. Pilot #182 остаётся открытым до проверки #185.

## Отклонённые альтернативы

- Удалить PR/CI из `--criterion`: теряется дословный contract.
- Подменить `PASS` полным завершением задачи: нарушает Definition of Done.
- Вывести delivery-критерии по ключевым словам: недетерминированно и даёт
  ложные классификации при изменении языка или формулировки.
- Разрешить push/PR модели: расширяет полномочия pilot без отдельного gate.
