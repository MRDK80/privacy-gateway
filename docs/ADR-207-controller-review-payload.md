# ADR-207: pinned contract и идентичность состояния в controller review

## Статус

Принято для задачи #207, epic #179.

## Контекст

Контроллер получает policy из закреплённого `base_sha`, но прежний review
payload не содержал refs, effective scope, permissions и repair budget.
`head_sha` обозначает фактический Git HEAD; при незакоммиченном diff он может
равняться `base_sha`. Проверенное состояние обозначено отдельным
`gate_evidence.snapshot.snapshot_commit` (ADR-200).

## Решение

Оркестратор передаёт контроллеру `contract` из своих закреплённых полей:
`issue`, `epic`, `acceptance_criteria`, `base_ref`, `base_sha`, `head_ref`,
`allowed_paths`, `permissions`, `max_repair_iterations`, `max_minutes` и
`task_class`. `remaining_repair_iterations` вычисляется как разность максимума
и текущей итерации. Это данные для проверки; policy по-прежнему загружается
из `base_sha`, а head не становится источником полномочий.

`reviewed_state.snapshot_commit` копируется из прошедшего gate evidence;
`head_sha` сохраняет прежнюю семантику и сверку с фактическим HEAD.
Production controller adapter до вызова Codex отвергает неполный contract,
расходящиеся значения идентичности, невалидный budget и отсутствующий
snapshot с `INVALID_REQUEST`. Проверка неизменности дерева остаётся в
оркестраторе. Executor report в review payload не передаётся.

Канонические схемы ролей, machine codes, exit codes и публичный `RunResult`
не меняются. Расширение review payload увеличивает prompt, который уже
передаётся через stdin (ADR-190).

На синтетическом request из `tests/test_codex_adapter.py` промпт контроллера
до изменения составлял 872 байта, после — 1608 байт (+736). Это измерение
фиксирует прирост для fixture; размер живого prompt зависит от policy и diff.
