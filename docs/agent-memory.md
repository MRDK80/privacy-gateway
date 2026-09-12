# Приватная память agent workflow

Каноническое решение и границы описаны в
[`ADR-166`](ADR-166-agent-learning-memory.md). Этот документ — операционный
контракт для локального хранилища и promotion кандидатов.

## Visibility policy

Public allowlist ограничен schemas, validators, role/checklist документацией,
sanitised rules и полностью синтетическими examples/tests. Всё остальное
deny-by-default: raw retrospectives, run logs, usage metrics, pending lessons,
controller notes, known pitfalls, локальные paths/configuration и реальные
payloads остаются вне публичной Git-истории. Сомнительная классификация всегда
означает private.

Private records разделены логически:

- raw task records — неизменяемые наблюдаемые итоги одного запуска;
- confirmed project facts — подтверждённые несколькими evidence sources факты;
- durable-rule candidates — только прошедшие классификацию и sanitization
  предложения, ещё не являющиеся доверенной policy.

История append-only: исправление добавляется новой записью; прежняя не
удаляется и не редактируется.
Полные retrospectives не загружаются в agent context: используется релевантная
выборка либо агрегированный `report`.

## Publication checklist

Кандидат не публикуется, пока все ответы не положительны:

1. Есть воспроизводимое evidence, а правило подтверждено повторно.
2. Удалены локальные пути, identifiers, operational details и свободный raw text.
3. Secret/privacy scan прошёл на candidate patch.
4. Содержимое безопасно для полного публичного раскрытия.
5. Независимый reviewer использовал policy из base SHA/read-only bundle, а не
   instruction-файлы candidate branch.
6. Изменение оформлено отдельным commit/PR; для instruction/configuration
   surface зафиксировано человеческое одобрение.

`AGENTS.md` не обновляется автоматически. Повторяемая механическая ошибка в
первую очередь предлагается как test или validator, а не как новая инструкция.
