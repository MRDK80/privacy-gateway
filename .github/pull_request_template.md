## Ветки и scope

- Уровень: TASK / ROADMAP
- Roadmap issue:
- Roadmap-ветка и SHA:
- Task issue:
- Task-ветка и SHA:
- Фактический head:
- Фактический base:
- Base SHA:
- Causal scope:
- Вне scope:

Разрешённые направления: task-ветка → соответствующая roadmap-ветка;
roadmap-ветка → `main`. Task PR непосредственно в `main` запрещён.

## Изменения

- Что изменено:
- Public/security contract:
- Tests:
- Docs/ADR/CHANGELOG:
- Breaking/release impact:

## Local quality gate

Обязателен полностью, включая documentation-only diff. Отмечайте пункт
только при наличии фактического результата.

- [ ] `python --version` — фактический вывод:
- [ ] `pytest -q` — exit code и passed/failed/skipped/xfailed/xpassed:
- [ ] `ruff check .` — exit code и результат:
- [ ] `mypy .` — exit code и число проверенных source files:
- [ ] `pre-commit run --all-files` — exit code и результат hooks:

## GitHub CI

Подтверждается только фактическими check runs текущего SHA. CI до merge и
post-merge CI указываются отдельно.

- [ ] Ubuntu latest / Python 3.11 — run/job ID и conclusion:
- [ ] Ubuntu latest / Python 3.12 — run/job ID и conclusion:
- [ ] Windows latest / Python 3.11 — run/job ID и conclusion:
- [ ] Windows latest / Python 3.12 — run/job ID и conclusion:
- [ ] pre-commit workflow — run/job ID и conclusion:
- [ ] `main-source-guard` — для roadmap PR; для task PR указать N/A:
- [ ] Post-merge CI нового SHA — run/job ID и conclusion либо ещё не выполнен:

## Review и риски

- GitHub Review (merge не является доказательством review):
- Остаточные риски:
- Exceptions:
- Статус (`TASK READY FOR REVIEW` / `TASK READY FOR REVIEW WITH EXCEPTIONS` /
  `TASK DONE` / `ROADMAP READY FOR RELEASE` /
  `ROADMAP READY FOR RELEASE WITH EXCEPTIONS` / `ROADMAP DONE` / `BLOCKED`):
- [ ] Нет реальных secrets/PII в коде, тестах и отчётах.
- [ ] Diff проверен на scope creep.
