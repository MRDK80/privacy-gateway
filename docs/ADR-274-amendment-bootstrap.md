# ADR-274 amendment: bootstrap до создания task PR

Дата: 2026-10-01. Epic: #248. Corrective scope #274.

## Решение

Checkpoint `2.0` явно отличает bootstrap от delivery: только в `PLAN`
допустим `pr: null`. Это не фиктивный PR и не разрешение GitHub write.
`RUN_TASK` включает bounded executor, полный local gate, независимый controller
и trusted commit/push/create-PR driver ADR-252. Его закрытый receipt содержит
`phase` и `delivery_identity` с точными полями identity checkpoint.

Supervisor допускает единственное закрепление PR и нового head SHA при
переходе из bootstrap `PLAN` в `RUN_TASK`. Repository, epic, task, base ref/SHA
и head ref неизменны. Новый PR — положительный integer, head — exact SHA.
Receipt проверяется против отдельно заново прочитанных live facts после
effect; model output или скопированный checkpoint не заменяют эти facts.
Последующие фазы не допускают изменения delivery identity.

При crash между созданием PR и сохранением checkpoint сначала необходим
read-only reconciliation `APPLIED` с тем же полным receipt, затем fresh live
revalidation. `UNKNOWN` запрещает retry. Для `NOT_APPLIED` bootstrap identity
должна всё ещё совпадать до повторного effect. Неожиданная смена head без
полного binding receipt останавливает supervisor. Невалидный receipt и
непроверенные live facts сохраняют pending intent для reconciliation.

Schema `1.0` остаётся читаемой без автоматического изменения. Явная чистая
функция миграции принимает только валидный checkpoint с уже существующим PR,
сохраняет identity/progress и меняет лишь версию; pending intent запрещает
миграцию. Она не сбрасывает PR в null и не применяется к реальному state
автоматически. Новый bootstrap создаётся planner, а не миграцией старой task.

Delivery Request `2.0` разрешает null PR только для `commit_task`, `push_task`
и `create_task_pr`. Ledger сохраняет версию новых requests; сериализация
legacy request `1.0` остаётся побайтово по полям совместимой. Merge, close и
update по-прежнему требуют настоящего PR и прежних exact gates. Bootstrap
операции не получают новых полномочий и проверяют тот же отдельный мандат.

## Границы и readiness

### Exact snapshot publication

Владелец выбрал публикацию именно reviewed snapshot commit, без переписывания
SHA в evidence. Узкий local `commit_task` adapter заново материализует snapshot
тем же алгоритмом ADR-197, сверяет commit/tree/diff с полным gate и
`reviewed_state.snapshot_commit` независимого controller. Затем импортирует
Git objects из disposable local snapshot и выполняет compare-and-swap только
task ref с исходного base SHA на проверенный commit. Roadmap/main refs не
меняются. Непосредственно перед созданием task PR, после подготовки body,
trusted transport guard заново читает scoped remote task ref и требует его
равенства reviewed snapshot SHA вместе с прежними base/task/authority checks.
Смена remote head запрещает POST; проверка после write не заменяет этот guard.
Commit сохраняет deterministic author/message/time ADR-197; это
не обычный пользовательский commit с новым SHA.

Перед перемещением ref index синхронизируется с уже проверенным tree, не
изменяя working files. Это разрешённая часть local commit; неизвестный исход
не объявляется NOT_APPLIED только по отсутствию нового HEAD. Reconciliation
читает ref и проверяет working tree, index и artifact; частичное состояние
требует решения владельца. Ref/index/ledger не являются одной транзакцией.
Подписанный commit не изготавливается; требования branch protection не
обходятся, а несовместимость unsigned snapshot должна блокировать push/merge.

Fresh approval/lifecycle проверяется непосредственно перед каждым local
mutation: import objects, `read-tree` и ref compare-and-swap. Проверка до
материализации snapshot не заменяет её: expiry/revocation во время подготовки
запрещает следующий mutation. После уже выполненного index write остановка
остаётся UNKNOWN и требует reconciliation, а не blind retry или rollback.

Это изменение внутренних supervisor/delivery contracts, не публичных
Library/pgw API. Оно не выдаёт и не активирует мандат, не меняет private
checkpoint владельца, не разрешает новый trust source или executor writes.
Concrete production adapters и owner-only установка требуют отдельной
проверки; успех synthetic contract tests не означает готовность live demo.
