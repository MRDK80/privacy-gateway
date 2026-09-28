# ADR-259: проверенные приватные lessons как недоверенные prompt hints

Дата: 2026-09-28. Задача: #259. Epic: #248.

## Статус

Принято и реализовано для внутреннего epic-runner. Публичные Library API,
`pgw`, trusted policy и `AGENTS.md` не меняются.

## Контекст

Приватная retrospective уже хранит redacted исходы сессий, но успешная
самооценка модели не доказывает решение, а сырой текст issue, PR, логов или
retrospective нельзя возвращать в prompt. Нужен ограниченный способ повторно
использовать только проверенный механический урок без расширения scope,
permissions, budget или authority.

## Решение

`tools/agent_verified_lessons.py` хранит lessons в owner-only append-only JSONL
вне worktree. Candidate содержит только ограниченные классификаторы, безопасный
симптом, диагностические шаги, минимальное исправление, применимость, метрики и
provenance epic/task/PR/head/merge. Переход в verified разрешён только когда
одновременно присутствуют полный deterministic gate, независимый controller
verdict, пять успешных PR checks и пять успешных post-merge checks. Head SHA
сверяется с ожидаемым SHA; incomplete, paused, stale и self-reported results
отклоняются.

Текст проходит allowlist-санитизацию, ограничения длины и числа шагов, а также
проверки известных secret и instruction-injection форм. Raw prompt, issue/PR
text, log, payload, path, secret и model output в формате отсутствуют. Повторная
запись того же lesson идемпотентна; несовпадающая запись с тем же id даёт
`LESSON_CONFLICT`.

Selector использует точное совпадение `task_class`, `problem_class` и всего
environment tuple, возвращает не более трёх `UNTRUSTED_VERIFIED_HINT`. Hint
явно не содержит permissions. Consumer добавляет этот отдельный envelope как
data после trusted policy и task contract; hint не участвует в digest мандата,
не меняет acceptance criteria, allowlist, tools, budget, approval или gates.
Нерелевантная задача получает пустой набор.

Disable создаёт append-only tombstone и немедленно исключает lesson из будущей
выборки, сохраняя provenance. Evaluation сравнивает baseline и hinted run по
iterations, duration и calls. Ускорение объявляется только при измеренном
неухудшении всех метрик и улучшении хотя бы одной; регрессия рекомендует
rollback/disable.

## Последствия и границы

- Lesson остаётся приватным и удаляемым вместе с private state согласно
  политике мандата; в Git и GitHub сохраняются только schema/код/синтетические
  tests.
- Verified означает достаточную provenance для hint, но не превращает hint в
  доверенную policy.
- Общее правило, validator или изменение `AGENTS.md` по-прежнему требует
  отдельного causal PR, полного gate, independent review и post-merge CI.
- Хранилище не защищает от владельца OS account и не является модельным
  fine-tuning.
