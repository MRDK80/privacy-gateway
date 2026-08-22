## Связи и scope

- Issue:
- Base SHA:
- Causal scope:
- Вне scope:

## Изменения

- Что изменено:
- Public/security contract:
- Tests:
- Docs/ADR/CHANGELOG:
- Breaking/release impact:

## Local quality gate

Обязателен полностью, включая documentation-only diff. Отмечайте пункт только при наличии фактического результата.

- [ ] `pytest -q` — результат и passed/skipped:
- [ ] `ruff check .` — результат:
- [ ] `mypy .` — результат и число проверенных файлов:
- [ ] `pre-commit run --all-files` — результат, включая detect-secrets:

## GitHub CI

Подтверждается только фактическими check runs. Если run ещё не существует, оставьте пункт незавершённым и не указывайте предполагаемый результат.

- [ ] Ubuntu latest / Python 3.11 — run/job ID и conclusion:
- [ ] Windows latest / Python 3.11 — run/job ID и conclusion:
- [ ] pre-commit workflow — run/job ID и conclusion:

## Review и риски

- GitHub Review (merge не является доказательством review):
- Остаточные риски:
- Exceptions:
- Статус (`READY FOR REVIEW` / `READY FOR REVIEW WITH EXCEPTIONS` / `BLOCKED` / `DONE`):
- [ ] Нет реальных secrets/PII в коде, тестах и отчётах.
- [ ] Diff проверен на scope creep.
