# ADR-303 addendum: исходный owner-wait budget anchor

Статус: решение v1 с уточнениями v2 принято владельцем 2026-10-17
(`call_b91b5ad28ecf408f8150b7592493ab82`); реализация и эксплуатационная
готовность подтверждаются отдельно. Это internal контракт будущих writers,
а не новый CLI/Library API, установленный owner command или runtime-мандат.

Решение дополняет [ADR-303](ADR-303-prepared-final-owner-gate.md), его
owner-preseeded addendum и утверждённый original final-owner source контракт.
Оно не создаёт owner artifacts, preseed, installation, invocation или новую
полномочность. Формальная валидность shape/digest не доказывает источники,
человеческое подтверждение, текущую authority или готовность deployment.

## FinalState2 и переходы

FinalState2 содержит ровно поля FinalState1 плюс `owner_wait`;
`schema_version` равен `2.0`, `record_digest` связывает всю запись.
Фазы, nullable поля, budget0..1 и blocked codes сохраняют прежнюю семантику.
`owner_wait` до первой публикации null либо закрытый объект ровно из семи полей:

- `invocation_id`: исходный lowerhex32 ID, не путь;
- `frame_digest`, `wait_identity_digest`, `runtime_config_digest`,
  `control_digest`: исходные tagged canonical SHA256 digests;
- `stage_started_at`, `deadline_at`: конечные неотрицательные JSON int/float,
  bool запрещён; исходное числовое представление не переписывается.

Первый nonnull anchor неизменяем в рамках исходного launch при **всех**
следующих фазах, включая PREPARING/BLOCKED и refresh candidate. Его нельзя
удалить, заменить новым frame/control/profile или продлить. Сравнивается
каноническое представление целиком: int100 и float100.0 не взаимозаменяемы.
Identity repository/epic/mandate_digest/launch_digest неизменна;
`calls_consumed` не уменьшается (0→1 разрешён). Новый полный phase graph
этим addendum не вводится. Pure `state2` допускает null и в фазе wait;
будущий standalone admission отдельно требует state2 **nonnull** anchor.

`state`, schema1 и legacy1–7 routes сохраняются. Нет автоматической migration,
backfill или восстановления anchor из удобного cursor/wait/frame. Внутренняя
pure проекция для проверки прежних полей не является сохранённым original
schema1, миграцией или подтверждением источников. Каждый будущий state2 writer
обязан применять transition validator; в этой pure реализации writer отсутствует.

## Будущая публикация и original sources

Используется только исходный `FINAL:FINAL_GATE`, без нового этапа, окна,
профиля или независимого owner budget. Публикация разрешена только реально
владеющему исходным Stage C контекстом после source-bound preparation, typed
APPLIED create receipt, exact PR CI, candidate/report, завершённых required
children и отсутствия неизвестных pending effects. Нужны actual current frame,
seed/session-control ownership и точные config/mandate/launch/control/profile.
Отсутствующий wait/CI record не создаётся и не backfill-ится.

Anchor связывает complete original frame digest и исходные identities; пути
выводятся только из исходного seed и проверенных ID/digests. Не перечисляются
случайные invocation files, не восстанавливается parent filename по digest,
не replay-ится consumed FD с подставленной командой. Установленная owner
procedure независимо pinned и admitted по original owner-source контракту.

FINAL CI identity имеет ровно repository, epic, roadmap_sha, main_sha,
merge_sha(nullable), pr с исходными typed значениями. Она восстанавливается
из anchored original request и candidate/create/assessment graph; digest
равен существующему wait.digest(identity). Unknown linkage/drift блокирует.
Нужен preexisting matching READY CI; READY не заменяет свежую CI проверку.
RESERVED/WAITING/FAILED, unresolved requests и READY-at-cap блокируют.

Исходный deadline anchor равен минимуму original frame.deadline_at,
persisted wait.deadline_at, mandate.expires_at и config.started_at плюс
original validated mandate.limits.max_duration_seconds. Копируется original
wait.started_at. Нет now+duration, нового Scope/frame или увеличения parent
bound. Время — Unix wall-clock seconds, durations — секунды; без округления.

Перед admission/reservation и после каждого завершённого чтения нужны
frame.started_at≤now<frame.deadline_at, wait.started_at≤last_seen_at≤deadline_at,
now≥last_seen_at, now≥wait.started_at, anchor.stage_started_at==wait.started_at,
now строго меньше min(anchor.deadline_at, latest wait.deadline_at,
mandate.expires_at, original launch expiry). Равенство deadline уже expiry;
rollback/bool/nonfinite/missing source блокируют. last_seen_at обновляется
только под исходным lock, deadlines никогда не увеличиваются. Текущие
STOP/PAUSE/revoke/control/iteration/followup ограничения имеют приоритет.

## Будущее single-attempt borrowed-stage admission

Новая отдельно проверенная no-create interface ещё не реализована. Она
фиксирована к approved installed owner procedure и исходному графу, без
caller stage/deadline/runner/callback/approved flag. Она не вызывает обычные
constructors, missing-record fallback, Scope/frame creation или attribute
injection. Lock order: session-control supervisor → seed → canonical wait,
исходные private regular single-link/nonblocking protections; live supervisor
не вытесняется, ambient descriptor не заимствуется. Каждая операция перечитывает
latest original wait под lock и сохраняет исходные counters/sequence/identity.

До **первого** свежего assessment read под wait lock проверяются identity,
cap и deadline; при attempts<original profile.max_ci_polls попытка расходуется,
CI записывается RESERVED, next_check_at=null. Неуспешная/неопределённая
persistence запрещает чтение. Каждый fixed remote command отдельно резервирует
свой исходный command budget до единственной transport operation. Cached READY
не является свежим ответом. Никаких новых counters/allowlist/endpoint/flags.

Только validated fresh success завершает ту же попытку READY без refund.
Pending либо definite invalid/non-success завершает FAILED и останавливается
без polling. Timeout/network/interruption/uncertain response/persistence
оставляет затронутый command и CI RESERVED, spent attempts сохраняются.
Нет DELAYS sleep/retry, alternate endpoint, restart retry или гарантированного
reconciliation. Ordinary owned runtime command/poll loops не изменяются.
Поздний отказ candidate/input не возвращает потраченный budget. Crash/partial
state блокирует, republish/new input/frame/profile/control не обновляет anchor.

Source graph Stage C, no-create interface и standalone composition остаются
отдельной будущей реализацией. Все прежние human/direct-digest/demo/original
receipt/pinned inventory/mount exclusion обязательства сохраняются. Согласованная
подмена тем же OS-owner остаётся прежней excluded boundary; shape не является
детектором человеческой истории. Atomic visibility не обещает cross-file crash
durability. Этот addendum не разрешает installation, invocation, activation,
owner writes, provider calls или merge.
