# #383: процесс разработки после #303

Это анализ и процессные правила, а не runtime optimization, новая authority
или запуск выделения обвязки. База анализа — roadmap merge
`ae9be1fc38d150eda2dc6230aea236a5de105d29` (PR #386, task #303).
Private originals остаются локально; пути, raw payload, diagnostics и ключи
в публичный документ не переносятся. Публичная постановка и delivery:
[#303](https://github.com/MRDK80/privacy-gateway/issues/303),
[#386](https://github.com/MRDK80/privacy-gateway/pull/386).

## Что измерено и что неизвестно

| Evidence | Фактический результат | Граница вывода |
| --- | --- | --- |
| Итоговый local gate V16 | 13 099,32 с; 3 676 PASS, 14 заранее допущенных SKIP; Ruff/mypy/hooks успешны | Один принятый финальный прогон, не длительность всей разработки |
| Отдельная post-merge synthetic task demo V2 | 8 373,75 с; один PASS, без SKIP/error/failure; child exit 0 | Synthetic scenario, не настоящий final owner approval и не production pilot |
| Предыдущая demo V1 | Остановлена по просьбе владельца, не принята | Owner STOP не классифицируется как runtime failure; USED сохраняется |
| Post-merge CI | Все пять exact checks нового roadmap SHA SUCCESS | Не заменяет local gate, review или applicable demo |

Значения длительности берутся из terminal receipts elapsed monotonic time;
остановленные и failed попытки не складываются с принятым прогоном как PASS.
Фактические primary receipts и независимые acceptance reviews сохранены у
оператора. Отсутствуют сопоставимый baseline полного рабочего процесса,
полная тарифицируемая usage/token статистика и единый измеренный total
всех review/repair/согласований/idle за историю #303. Эти числа не
восстанавливаются из названий V1–V16, количества файлов или timestamps чата.
Полного before/after эксперимента процессных изменений #383 пока нет;
процент ускорения и экономия токенов не заявляются.

## Повторные затраты и причинные изменения

- Частные source/static PASS не доказывали общую composition. Теперь до кода
  описывается entry-to-terminal сценарий и его швы; review частей заканчивается
  review полной композиции. Неизвестный fit раскрывается до одноразового запуска.
- В #303 пришлось отдельно исправлять admission active-namespace fixture до
  acquisition shared producer. Поэтому preflight проверяет setup/order/shared
  fixtures и consumers, не только изменённый файл или тестовый call.
- Для generated source важно проверять decoded bytes с реальным config/path
  context. Format/type ошибки оператора требуют связанных быстрых проверок,
  а не нового дорогого whole-suite запуска на каждую механическую строку.
- В observer обнаруживалось сравнение с прежним clock binding. При reuse
  изменённые identity/argv/input/clock bindings перечисляются целиком; старый
  PASS не выдаёт новый effect. On-disk receipts и counters не переписываются.
- Повторные primary reads нужны для authority/freshness, но одинаковое чтение
  неизменного immutable input не следует заказывать без причины. Сохраняйте
  receipt и основание применимости; invalidation по source/base/environment
  drift обязательна. Это не разрешает cached authority или сокращение runtime
  revalidation. Количество устранённых reads пока не измерено.
- Queued message, начатый turn и progress — три разных факта. Для разрешённого
  idle исполнителя нужен работающий follow-up, для running — observation,
  для terminal — actual result review. Не создавайте дубль из-за тишины.
- Один current handoff отделяется от неизменяемой истории. При конфликте
  сводок проверяются primary facts; старые поля не становятся текущим статусом
  только потому, что лежат выше в большом JSON.

Эти изменения закреплены в CONTRIBUTING.md, краткой AGENTS.md и supervision
runbook. Существующий конечный owner-approved repair packet не возникает
автоматически из #383; #374, reasoning escalation и issue-wide consumed
attempts сохраняются. Второй однотипный сбой одной цепочки вызывает causal
review до следующего разрешённого repair, а не увеличение разрешённого числа
попыток. Scoped checks и полный финальный gate сохраняют разные значения.

## Что на самом деле ограничивают bytes и reads

| Порог | Происхождение и единица | Риск и неизвестность |
| --- | --- | --- |
| 8 МиБ owner-frame total | Историческая инженерная граница; owner-approved 16 МиБ 23 октября, затем 64 МиБ 25 октября | Не внешний лимит платформы и не текущий production total |
| 64 МиБ owner-frame total | `OwnerFrameSources.MAX_TOTAL_BYTES`; cumulative raw I/O одного нового instance | Все повторные reads/revalidation расходуют тот же counter; не память процесса, не whole-run cap и не доказательство полного fit |
| 16 КиБ/frame, 4 096 entries | Отдельные границы frame/graph | Увеличение total не отменяет их и source/identity guards |
| 512 read commands | Абсолютный ceiling `max_read_commands` owner wait-profile по ADR-299; конкретный profile может быть ниже | Не GitHub rate limit и не число уникальных URL: новый successful fresh read расходует новый ordinal |

См. [ADR-303 capacity](ADR-303-owner-frame-capacity.md),
[ADR-299](ADR-299-durable-bounded-waits.md) и production
`tools/agent_epic_wait_frame.py`. Незавершённый transient read возобновляется
с прежним ordinal/attempts только при exact command digest. HTTP retries,
CI polls и fresh command ordinals не суммируются как одна метрика; deadlines
ограничивают их отдельно. READY не используется как cached authority.

64 МиБ — утверждённая allocation после исчерпания предыдущего конечного
ceiling в targeted prefix, а не измеренный upper bound полного пути.
Типичный и худший raw-byte/read расход полного сценария отдельно не
измерены; universal запас не известен. Оценка до запуска должна учитывать
число повторных обходов, объём source graph, revalidation и prompt overhead,
сохраняя отказ до следующего недопустимого чтения. Exhaustion не разрешает
EOF probe, counter reset, phase reinitialization или незаметный retry.
Изменение будущего лимита требует собственного основания и approvals;
#383 не меняет production thresholds, schemas и существующие allocations.

## Сравнение вариантов и bounded runtime follow-ups

Это постановки отдельных follow-ups для review очереди, не утверждённые
runtime changes. Каждая получает свой causal PR, regression/failure-path
proof, независимый controller, полный gate и exact CI. До production-кода
нужны применимые architecture/security решения. Отдельный проект PrivacyAgents
не создаётся и не переносится этой задачей.

1. **Приоритет 1: измерить повторное чтение owner-frame graph.** Baseline:
   64 МиБ cumulative allocation и принятый synthetic lifecycle, полного
   byte/read breakdown нет. Сначала собрать bounded synthetic test-only
   метрики scan/revalidate/phase на прежнем exact сценарии; не логировать
   payload. Цель: отдельные колонки frame bytes/raw read calls и GitHub fresh
   command ordinals/attempts/CI polls по фазам и выделение
   доказанно повторных immutable reads. Freshness/identity/STOP/expiry проверяются
   как прежде. При отсутствии избыточности оптимизацию не внедрять. Снижение
   raw I/O возможно, но не обещается. Runtime telemetry/schema сюда не включать.
2. **Приоритет 2: оценить reuse неизменных исходных фактов.** Зависит от
   измерения 1. Baseline: подтверждённый breakdown. Выбрать одну доказанную
   повторную операцию и документировать invalidation до реализации. Цель:
   меньше raw reads на том же сценарии с теми же guards; cached authority,
   пропуск revalidation и counter refunds запрещены. Negative tests на drift,
   revoked/expired authority и changed graph обязаны сохранять fail-closed.
   Стоимость выше process-only reuse, так как затрагивает trusted sources.
3. **Приоритет 3: упростить fixture/operator подготовку.** Baseline:
   actual setup/teardown и source hashes принятого сценария. Сначала измерить
   setup отдельно от call и review. Цель: уменьшить подтверждённое повторное
   построение immutable окружения, сохранив свежие private output/identity,
   containment, cross-test isolation и source conservation. Изменение shared
   fixture order/lifetime проверяется всеми consumers, не одиночным helper.
   Универсальный новый executable framework не вводится без отдельного design.

Process-only reuse принятого immutable evidence дешевле runtime cache и не
меняет production trust model, но требует доказанной применимости. Объединение
независимых read-only проверок экономит round trips только если сохраняет
полноту и нужную свежесть. Упрощение fixtures требует отдельного equivalence
proof. Пересмотр thresholds меняет ресурсную экспозицию и authority и потому
дороже; он не является автоматическим лечением повторного чтения. Для всех
вариантов отсутствующее измерение остаётся UNKNOWN, а не обещанным улучшением.

## Пример следующего действия

Для будущей notification task сначала фиксируют настоящий entry, synthetic
question и mock transport, ожидаемую одну отправку, restart/reconciliation и
terminal proof. До дорогого запуска считают reads/calls/bytes/time с полным
input и teardown; отдельно перечисляют actual Telegram и pilot approvals.
Текущий статус содержит phase/последний proof/blocker/next, точные refs,
consumed budgets, process identity и pending effects. Исторический PASS не
выдаётся за актуальный source или разрешение на новый запуск.
