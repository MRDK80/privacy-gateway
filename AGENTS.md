# AGENTS.md

Обязательные инструкции для автоматизированных исполнителей (coding agents) в `privacy-gateway`. Полный источник правил — [`CONTRIBUTING.md`](CONTRIBUTING.md); этот файл не заменяет и не переопределяет его.

- Сначала прочитай issue, связанные ADR, `SECURITY.md` и документы репозитория, потом меняй файлы.
- Работай от актуального `main`, зафиксируй base SHA, создай отдельную ветку.
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

- Приводи первичные результаты: passed/skipped, число файлов `mypy`, вывод `ruff`, итог pre-commit с `detect-secrets`.
- Локальные результаты указывай отдельно от GitHub check runs; CI подтверждай только check runs с conclusion и run/job ID.
- Не заявляй review, если GitHub Review не зарегистрирован. Merge — не review.
- Не используй `DONE`, если полный локальный gate или exact CI не подтверждены; используй `BLOCKED` или `READY FOR REVIEW WITH EXCEPTIONS`.
- Не закрывай roadmap- и audit-issues автоматически; не меняй tags, releases, branch protection и настройки репозитория.
