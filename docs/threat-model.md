# Модель угроз

Актуальная модель угроз и политика безопасности поддерживаются в одном
документе — [`SECURITY.md`](SECURITY.md).

Прежняя таблица статусов уровня Э1 удалена: она описывала как
запланированные контрмеры, которые реализованы в Э2, Э4, Э7 и Э8, и
расходилась с `SECURITY.md` как второй источник правды.

## Куда смотреть

| Вопрос | Раздел `SECURITY.md` |
|--------|----------------------|
| Границы защиты | «От чего защищает», «От чего не защищает» |
| Границы доверия и артефакты на диске | «Границы доверия», «Что попадает на диск» |
| Целостность пары артефактов и `manifest_sha256` | «Целостность пары артефактов» |
| Порядок проверок и строгий режим `restore` | «Порядок проверок restore», «Строгий режим restore» |
| Ответ внешнего обработчика как недоверенный вход | «Ответ LLM как недоверенный вход» |
| Безопасность отчёта и атомарность записи | «Безопасность отчёта restore», «Атомарность restore» |
| Хранение и ротация ключа | «Обращение с ключом», «Ротация ключа» |
| Безопасность YAML-конфига | «Безопасность YAML-конфига маршрутизации» |
| Принцип fail closed | «Почему fail closed» |
| Библиотечная интеграция | «Библиотечное использование» |
| Сообщить об уязвимости | «Сообщить об уязвимости» |

Формат токенов и классификация искажённых токенов описаны в
[`token-format.md`](token-format.md), архитектура конвейеров — в
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Proposed autonomous launch profile (#295)

This addition concerns the local development runner, not the product PII
pipeline. [ADR-295](ADR-295-autonomy-launch-contract.md) is a proposed contract
whose design has owner acceptance but still requires integration and
implementation. It does not change
installed authority or the product security guarantees in `SECURITY.md`.

Protected assets include owner authorization, trusted policy provenance,
exact reviewed snapshots, delivery receipts, consumed budgets and private
state. Issue/PR text, generated plans, task heads, model output, role hints
and external responses remain untrusted. The attacker may inject these inputs
or cause transport failures, stale facts and interrupted execution; control
of the owner OS account, trusted installed bytes or process memory is outside
the baseline boundary. Digests bind identity; they do not authenticate an
untrusted owner approval source.

| Threat | Proposed control and required evidence |
| --- | --- |
| Epic number or generated plan grants itself authority | Owner-only pinned launch approval; deterministic scope/criteria admission; injection test proves refusal |
| Generated hint changes role permissions or review policy | ADR-259 untrusted hint boundary, sanitization and bounded selection; policy promotion requires separate review/approval |
| Synthetic demo impersonates owner observation | Explicit approved demo class and SHA-bound labelled evidence; real-input receipts and final demo remain owner-only |
| Wait/restart renews authority or refills budgets | Preserve original expiry, counters and pending intents; revalidate on wakeup and before effects; expiry/revoke tests prove zero later writes |
| Lost session starts a second writer or repeats unknown write | Single-runner ownership, process/start identity and canonical reconciliation; UNKNOWN retains stop boundary |
| Final approval is reused after head/base changes | Persistent human gate binds exact main/roadmap SHA, PR and demo; changed evidence invalidates approval |
| Status/retrospective leaks private input or diagnostics | Closed bounded status and sanitized private evidence; no raw payload/log/path in public artifacts |

Residual limits: a verified synthetic run does not prove production readiness,
and a live process does not prove progress or cross-turn wakeup. Installation,
sandbox/observation capabilities and production pilot must be checked on the
actual host. Separate atomic writes are not a transaction across checkpoint,
ledger, Git and GitHub; exactly-once and additional crash durability are not
promised. Stopped #131 state is neither migrated nor activated by this proposal.
