# ADR-166. Приватная память и метрики agent runs (#166)

Дата: 2026-09-11. Epic: #162.

## Контекст

Результаты executor/controller полезны для поиска повторяемых механических
ошибок, но raw retrospectives, run logs, usage metrics, pending lessons и
known pitfalls небезопасно автоматически помещать в публичную Git-историю.
Instruction-файлы являются доверенной управляющей поверхностью, а issue, PR,
комментарии, логи и head tree — недоверенными данными.

## Решение

`tools/agent_memory.py` хранит по одной валидируемой JSONL-записи для каждого
terminal run оркестратора #165. Запись содержит только наблюдаемые результаты:
task class, SHA, длительность, число вызовов ролей, `iterations`, repair loops,
verdicts, machine-code failures, числа findings, false positives и ручных
вмешательств. Usage записывается только из поддерживаемого источника; иначе
явно используется `source: unavailable` и `units: null`.

Хранилище по умолчанию —
`$XDG_STATE_HOME/privacy-gateway/agent-memory/retrospectives.jsonl`. Каталог
внутри working tree отклоняется. Каталог и файл получают owner-only права;
записи дописываются через `O_APPEND`, не перезаписываются API инструмента и
проходят строгую валидацию до записи. Raw tool output, model transcripts,
controller notes, свободный текст и chain-of-thought в формате отсутствуют.

Команда `python tools/agent_memory.py report` возвращает только агрегаты по
task class: количество задач, средние duration/iterations/findings, число
failures и доступность usage. Она не возвращает сырые записи и не предназначена
для загрузки всей истории в agent context.

Публичный репозиторий хранит только эту ADR, schema, policy, валидатор и
синтетические tests. `.gitignore` — дополнительная защита; перед ролью
оркестратор по-прежнему fail-closed проверяет tracked/staged private patterns.

## Promotion pipeline

Продвижение знания выполняется отдельно:

```text
private retrospective → classification → sanitization → secret/privacy scan
→ evidence-backed candidate patch → independent review → отдельный commit/PR
```

Один эпизод остаётся приватным наблюдением. Повторяемое подтверждённое правило
может стать кандидатом; механическое условие предпочтительно переносится в
test/validator. Publication gate разрешает patch только одновременно
evidence-backed и безопасный для полного публичного раскрытия. Изменения
`AGENTS.md`, role/skill definitions и agent configuration не применяются самим
executor и требуют отдельного независимого человеческого review. Controller
использует policy из закреплённого base SHA или локального read-only bundle и
не доверяет instruction-файлам candidate/head.

## Последствия и границы

JSONL append защищает от штатной перезаписи инструментом, но не от владельца ОС
или crash между отдельными append. Хранилище не предназначено для secrets, PII,
plaintext payloads, key material или подробностей ещё не исправленных слабых
мест. При сомнении artifact остаётся приватным. Инструмент не меняет `AGENTS.md`
автоматически и не входит в публичный CLI/Library API Privacy Gateway.

**Статус:** действует.
