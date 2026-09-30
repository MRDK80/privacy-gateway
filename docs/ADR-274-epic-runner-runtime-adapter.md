# ADR-274: production runtime adapter для `resume`

Дата: 2026-09-29. Задача: #274. Epic: #248.

## Решение

`tools/agent_epic_loop.py resume` загружает закрытый runtime-config из private
state вне repository и продолжает checkpoint с первой незавершённой фазы.
Supervisor последовательно вызывает отдельные argv-команды с абсолютным
executable path и без shell для
live identity, самой фазы и reconciliation. Команды ограничены timeout и
размером stdout/stderr; принимается только один JSON-объект установленной
формы.

Runtime adapter не является новым источником полномочий. Команды записи
обязаны вызывать trusted delivery driver ADR-252, который перед каждым
side effect повторно проверяет mandate lifecycle, digest, allowlist операции,
repository/epic/task/PR, refs, exact SHA и применимый gate. Read-only фазы
обязаны использовать structural discovery, deterministic gate и independent
controller. Текст issue/PR и stdout команды не могут изменить checkpoint или
расширить мандат.

`live_command` возвращает ровно identity checkpoint и, когда уже известен,
`merge_sha`. Phase command возвращает `APPLIED` и закрытый receipt ровно своей
фазы (для `DEMO` действует отдельная SHA-bound schema) либо
`BLOCKED` с machine code и `null` receipt. Ожидаемые `CI_PENDING`, неготовая
demo и иные доказанные остановки очищают phase intent и не классифицируются
как неизвестный outcome. Crash, timeout, transport failure, невалидный JSON
или пустой/неполный receipt сохраняют fail-closed поведение. Machine code
ограничен коротким uppercase ASCII identifier без свободной диагностики. При сохранённом
pending intent сначала вызывается phase-specific reconciliation; повтор
разрешён только после `NOT_APPLIED`.

Один вызов продолжает фазы до `NEXT_TASK` либо первой безопасной остановки.
Сохранённый `PAUSED_RATE_LIMIT` возвращается без вызова runtime adapter:
сначала отдельный rate-limit resume обязан заново проверить quota и live
identity согласно ADR-250, и только затем разрешено продолжать фазы.
Команда `NEXT_TASK` должна сначала выполнить разрешённые `close_task` и
`update_epic`, затем пересчитать structural queue. Если есть следующая task,
она создаёт новый отдельный checkpoint через planner/handover; если очередь
исчерпана, она передаёт управление существующему final driver ADR-256. Таким
образом task и final-roadmap delivery остаются разными SHA-bound state
machines, а runtime adapter только связывает их.

## Границы

Runtime-config запрещён внутри repository, не содержит credentials и не
коммитится. State отклоняется внутри declared root, checkout самого supervisor
и любого родительского Git worktree с `.git` directory/file, даже при ложном
`repository-root`. Команды могут запускать только owner-only trusted scripts из
реального owner-only `<state>/adapter-bin` через абсолютные пути текущего
Python и script;
после проверки adapter сохраняет доверенный путь текущего interpreter
(сохраняя virtualenv), а не executable alias из config, и resolved
script path.
цепочка родителей state проверяется на защищённость от переименования другим
OS account (system-owned sticky temporary directory допустим; system owner
определяется по корню filesystem для поддержки UID mapping). Scripts находятся
непосредственно в `adapter-bin`, вложенные каталоги запрещены.
repository executable,
`-c`, PATH lookup и произвольные binaries запрещены. Production adapter
запускается только на POSIX: проверяются owner
и mode config/state; каждая команда выполняется в Bubblewrap PID namespace с
`--die-with-parent`, а stdout/stderr ограничиваются capped readers. Фаза
`RUN_TASK` делегирует executor существующему
sandbox adapter ADR-225 с minimal-file Bubblewrap boundary; delivery-фазы
остаются в узком driver ADR-252. На Windows
он fail-closed возвращает `RUNTIME_ADAPTER_UNSUPPORTED`, поскольку этот runtime
не реализует проверку ACL. Произвольный shell, `shell=True`, fallback на более широкий token,
обход branch protection, force push и повтор неизвестного write запрещены.
Публичные Library API, `pgw`, существующие product JSON contracts и форматы
токенов не меняются.
