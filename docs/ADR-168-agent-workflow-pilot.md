# ADR-168. Пилот и первая калибровка agent workflow (#168)

Дата: 2026-09-12. Epic: #162.

## Контекст и baseline

Исходный ручной процесс задачи #159 израсходовал около 55% доступного окна и
требовал повторных Git-проверок и ручных вмешательств. Это единственный
доступный baseline usage: для пилотных запусков provider/host usage signal не
предоставлен, поэтому он фиксируется как `unavailable`, а экономия не
декларируется. Сырые значения длительности, usage и отдельные retrospectives
остаются только в owner-only private storage и не публикуются в этом ADR.

## Воспроизводимый пилот

`tests/test_agent_workflow_pilot.py` выполняет полный локальный цикл
`executor → deterministic gate → controller` для трёх представительных классов:

| Класс | Синтетический public patch | Проверяемая граница |
|---|---|---|
| docs | документ | недоверенный generated text остаётся данными |
| test-only/small bug | regression test | causal test-only scope |
| developer tooling | tool helper | отдельные executor/controller sessions и trusted base policy |

Каждый запуск создаёт валидную retrospective вне worktree, проверяет
сопоставимый aggregate report, явный `usage: unavailable` и предел не более
двух repair-loop. Fixtures полностью синтетические; реальные payloads,
credentials, PII и key material не используются.

Остальные failure-path evidence закреплены существующими тестами:

- два неуспешных repair-loop приводят к `FAIL_ESCALATE`;
- staged/tracked private artifacts блокируются до controller/CI;
- изменённые в head `AGENTS.md` и protected configuration не применяются как
  policy и требуют человеческой эскалации;
- issue text, PR metadata, check names/log metadata и generated diff не могут
  расширить permissions или approval gates;
- PR verifier ждёт terminal success всех checks, повторно сверяет точный head
  SHA и требует отдельный human review для protected paths;
- promotion candidate отклоняется до classification, sanitization,
  secret/privacy scan, independent review и publication-safety verdict.

## Калибровка

Пилот подтвердил правильность разделения доверенной base policy и недоверенных
task inputs. Он также выявил неполное совпадение private path deny-list с
видимостью из epic: `known-pitfalls/`, `usage-metrics/` и `config.local/`.
Минимальная корректировка добавляет эти пути в `.gitignore`, pre-controller
проверку и PR verifier. Regression tests сначала воспроизводят каждый путь, а
затем подтверждают fail-closed результат `PRIVATE_ARTIFACT_STAGED` либо
`PRIVATE_ARTIFACT`.

Новых repository-wide инструкций пилот не обосновал: `AGENTS.md` не меняется.
Механическое правило принадлежит validators/tests, а не журналу инструкций.
False positives классифицируются явно: безопасный публичный синтетический
fixture под `tests/fixtures/` не считается private artifact только из-за слова
`retrospective` в имени.

## Ограничения

- Пилот детерминирован и provider-neutral; он не измеряет качество конкретной
  LLM и не создаёт сопоставимого с #159 usage observation.
- CODEOWNERS без enforcement не заменяет фактический GitHub Review.
- Успешный test suite не заменяет exact PR CI и post-merge CI нового SHA.
- Raw retrospectives, run logs, usage metrics и local configuration не являются
  evidence для публикации и остаются deny-by-default.

**Статус:** действует.
