# ADR-254: пауза epic-runner по квоте Codex

Дата: 2026-09-28. Задача: #254. Epic: #248.

## Статус

Принято для локального supervisor epic-runner.

## Решение

Supervisor принимает только структурированный snapshot метода Codex App Server
`account/rateLimits/read`. Окно считается исчерпанным при `usedPercent >= 100`;
первичное и вторичное окна обрабатываются независимо, поэтому одновременно
могут быть исчерпаны пятичасовая и недельная квоты. Для возобновления выбирается
самый поздний известный `resetsAt`. Если хотя бы у исчерпанного окна время reset
неизвестно, время не придумывается и требуется последующая read-only проверка.

Пауза сохраняется в private checkpoint со статусом `PAUSED_RATE_LIMIT`, не
завершая и не повторяя текущую фазу. Следующая проверка ограничена интервалом
poll; после restart используется тот же checkpoint. Перед возвратом в `READY`
заново сверяются repository/epic/task/PR, refs и SHA с live GitHub facts, а
квоты повторно читаются. Их изменение даёт fail-closed stop.

Только коды App Server `rateLimitExceeded` и `usageLimitExceeded` могут начать
эту паузу. Ошибки сети, authentication, overload, billing/spend control и
неизвестные ошибки остаются обычными ошибками и не маскируются как квота.
Метод расходования reset credits никогда не вызывается.

## Не входит

Покупка кредитов, расходование reset credits, transport App Server, consumer
demo (#255), публичные Library/CLI/JSON contracts, version bump и release.
