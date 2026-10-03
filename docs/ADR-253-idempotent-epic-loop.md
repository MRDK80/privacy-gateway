# ADR-253: идемпотентный supervisor task-фаз epic-runner

Дата: 2026-09-27. Задача: #253. Epic: #248.

## Статус

Принято для последовательного локального запуска фаз одной task.

## Решение

`tools/agent_epic_loop.py` хранит закрытый checkpoint версии `1.0` в private
state вне repository и проводит одну task только по фиксированной цепочке
`PLAN -> RUN_TASK -> PR_CI -> MERGE -> POST_MERGE -> DEMO -> TASK_DONE ->
NEXT_TASK`. Identity repository/epic/task/PR, refs и SHA сверяется с заново
полученными live facts перед каждым переходом. Перескок фазы, смена identity,
неизвестное поле или отсутствие merge SHA после merge дают fail-closed stop.

Неблокирующий process lock допускает только один supervisor для state
directory. Перед effect checkpoint атомарно фиксирует `pending_phase`, после
проверенного receipt — завершённую фазу. Уже завершённая фаза возвращает
`NO_OP`. После crash или неоднозначного effect переход не повторяется, пока
read-only reconciliation не докажет `APPLIED` или `NOT_APPLIED`; `UNKNOWN`
даёт `ESCALATE_UNKNOWN_OUTCOME`. Атомарность checkpoint не объявляется общей
транзакцией с Git или GitHub.

Supervisor не получает новых полномочий: фактические writes делегируются
узким callbacks существующего delivery driver ADR-252, а gates и SHA остаются
обязанностью соответствующих фаз. Operator status показывает текущую и
следующую фазу и безопасную команду resume без issue/PR text, логов, prompts,
секретов или иных свободных данных в private checkpoint.

## Не входит

Codex quota/reset (#254), реализация и подтверждение consumer demo (#255),
финальный roadmap PR и завершение epic (#256), transport credentials,
публичные Library/`pgw` API, существующие JSON contracts, version bump и
release.
