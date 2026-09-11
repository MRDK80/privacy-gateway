# AGENTS.md

Обязательная краткая policy для coding agents во всём репозитории
`privacy-gateway`. Полный процесс разработки и Definition of Done находятся в
[`CONTRIBUTING.md`](CONTRIBUTING.md); при расхождении приоритет у него.

## Назначение и trust boundary

- Privacy Gateway — local-first инструмент для обнаружения и псевдонимизации
  PII и секретов до передачи текста внешней LLM.
- Восстановление ответа выполняется локально по зашифрованному манифесту и
  ключу из системного keyring.
- За trust boundary разрешено передавать только защищённый текст,
  предназначенный внешнему потребителю, а не весь `PreparedPayload`.
- Нельзя передавать наружу plaintext, исходный текст, PII, секреты,
  восстановленные значения, key material, `manifest.json`, `route.json`
  или чувствительные логи и диагностику.
- Псевдонимизация обратима и не является анонимизацией. Восстановленный ответ
  снова считается чувствительными данными.

Канонические источники security-гарантий и ограничений:
[`SECURITY.md`](SECURITY.md), [`docs/SECURITY.md`](docs/SECURITY.md),
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) и
[`docs/threat-model.md`](docs/threat-model.md).

## Публичные контракты

- Публичный Library API определён в
  [`docs/LIBRARY_API.md`](docs/LIBRARY_API.md). Остальные модули пакета
  являются internal implementation details.
- Единственный источник актуального CLI-синтаксиса — `pgw --describe`;
  не копируй каталог команд и параметров в документацию.
- JSON-контракт и CLI-интроспекция определены в
  [ADR-150](docs/ADR-150-cli-json-contract.md) и
  [ADR-151](docs/ADR-151-cli-introspection.md).
- Не меняй попутно публичные CLI/Library API, JSON schema, machine codes,
  process exit codes, stdout/stderr contracts или форматы токенов и маршрутов.
  Такие изменения требуют отдельного решения, ADR и contract tests.

## Обязательный local quality gate

При первоначальной настройке репозитория выполни `pre-commit install`.
Перед local gate убедись, что `pre-commit` доступен. Если инструмент
отсутствует, верни `BLOCKED` и не меняй dependencies без
отдельной issue. Полный gate обязателен и для documentation-only diff:

```bash
pytest -q
ruff check .
mypy .
pre-commit run --all-files
```

`git diff --check` — дополнительная проверка, не заменяющая четыре команды.
Фактические результаты local gate и GitHub CI фиксируются раздельно; правила
отчётности, Python policy и exact CI описаны в
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Security invariants

- Ключи, исходные и восстановленные значения не логируются, не включаются в
  ошибки и не попадают в `repr`/`str`.
- Не ослабляй детекцию, независимую валидацию, fail-closed поведение или
  security-тесты. Используй только синтетические fixtures.
- Атомарная видимость файла не означает crash durability. Общая транзакция
  всех артефактов `prepare` не обещается.
- Cleanup временного plaintext выполняется best-effort: отказ `unlink` может
  оставить временный файл.
- Ротация не является транзакцией keyring; компрометация локального процесса,
  OS account или raw keyring backend находится вне базовой trust model.
- Формулировки сверяй с security-документами и действующими ADR; не усиливай
  гарантии по сравнению с каноническими источниками.

## Минимальный causal diff

- Одна issue — один ограниченный PR; не делай попутный рефакторинг.
- До изменений прочитай roadmap issue, task issue, связанные ADR и документы.
- Для подтверждённого дефекта сначала добавь regression/failure-path test,
  затем исправление.
- Security- и contract-решения документируются до production-кода.
- В documentation-задаче не меняй runtime, публичные контракты, версию,
  CHANGELOG, workflows или dependency metadata.

## Git workflow

```text
main
  <- roadmap/<roadmap-issue>-<slug>
       <- <task-branch>
```

Разрешённые направления PR:

```text
<task-branch> -> roadmap/<roadmap-issue>-<slug>
roadmap/<roadmap-issue>-<slug> -> main
```

`<task-branch> -> main` запрещено. Task-ветка создаётся от соответствующей
roadmap-ветки; при обновлении `main` сначала обновляется roadmap. Проверяй
фактические head, base и SHA, а не только имена веток.

## Запрещённые автоматические действия

Без явного подтверждения владельца не выполняй merge и не закрывай issues.
Не делай force push, не удаляй `main` или roadmap-ветки, не создавай и не
перемещай tags, не редактируй releases, branch protection, visibility или
настройки репозитория. Не объявляй CI или local gate успешными без первичных
результатов. Полные workflow, handover и статусные правила находятся в
[`CONTRIBUTING.md`](CONTRIBUTING.md).
