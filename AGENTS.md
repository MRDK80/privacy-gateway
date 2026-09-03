# AGENTS.md

Обязательные инструкции для автоматизированных исполнителей (coding agents) в `privacy-gateway`. Полный источник правил — [`CONTRIBUTING.md`](CONTRIBUTING.md); этот файл не заменяет и не переопределяет его.

- Сначала прочитай roadmap issue, task issue, связанные ADR, `SECURITY.md` и документы репозитория, потом меняй файлы.
- `main` — релизная ветка; roadmap-ветка создаётся от актуального `main`; task-ветка создаётся от соответствующей roadmap-ветки.
- Разрешены только PR `task -> roadmap` и `roadmap -> main`. Проверяй фактические head, base и SHA через GitHub.
- Не выполняй прямой push в `main` или существующую roadmap-ветку; не применяй к ним force push и не удаляй их до завершения установленного процесса.
- Если `main` изменился, сначала обнови roadmap-ветку, затем task-ветки.
- Соблюдай causal scope: одна issue — один ограниченный PR. Не расширяй scope и не делай попутный рефакторинг.
- Security- и контрактные решения фиксируй документально до production-кода.
- Для подтверждённого дефекта сначала добавь воспроизводящий regression/failure-path тест, затем исправление.
- Не используй реальные secrets, credentials, PII и пользовательские пути; fixtures только синтетические.
- Выполни полный gate даже для documentation-only diff:

```bash
pytest -q
ruff check .
mypy .
pre-commit run --all-files
```

- Приводи первичные результаты: exit codes, passed/failed/skipped/xfailed/xpassed, число файлов `mypy`, вывод Ruff и итог всех pre-commit hooks.
- Локальные результаты отделяй от GitHub check runs; CI подтверждай только для текущего SHA с conclusion и run/job ID.
- После task merge проверяй post-merge CI нового SHA roadmap-ветки; после roadmap merge — post-merge CI нового SHA `main`.
- Используй только статусы `TASK READY FOR REVIEW`, `TASK READY FOR REVIEW WITH EXCEPTIONS`, `TASK DONE`, `ROADMAP READY FOR RELEASE`, `ROADMAP READY FOR RELEASE WITH EXCEPTIONS`, `ROADMAP DONE` или `BLOCKED`.
- Статус `DONE` без префикса запрещён. Merge не является доказательством GitHub Review.
- Не закрывай roadmap- и audit-issues автоматически; не меняй tags, releases, branch protection, visibility и настройки репозитория без отдельного подтверждения владельца.
