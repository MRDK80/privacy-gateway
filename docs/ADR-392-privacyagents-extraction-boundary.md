# ADR-392: граница выделения PrivacyAgents

Task #392, epic #391. Статус: концептуальный design утверждён владельцем
03 ноября 2026; документационная доставка ещё не завершена. Направление B,
semantic allocation и отдельная main-based roadmap подтверждены. Это не
принятие новой executable schema, не code transfer, установка,
runtime activation, production pilot или разрешение main merge.

## Цель и исходное содержимое

Публичный MRDK80/PrivacyAgents предназначен для общего development orchestration:
координатор, роли и adapters, delivery, gate/evidence, waits/quota/recovery,
supervision и verified learning. PGW сохраняет продукт обработки PII,
криптографию/keyring, CLI/Library API, product security и product tests/examples.

Анализ выполнен на source commit
`e0b13d1128c91350092a5f146343edfc02c324a8`, tree
`d4cd93b3cbb27723c7088ed21b60ca76f952316f`. Этот же tree имеет roadmap merge
`27c8d2b458712c44b54d5fedd2ca78c398b6246e` после #383. Он является публичным
источником анализа, а не parent документационной roadmap #391.
Отдельная roadmap создаётся от актуального main после fresh ref проверки;
она не наследует unmerged #294 code и не обходит его final main gate.

## Почему недостаточно скопировать tools

Текущий installer извлекает весь tools/docs/schemas из одного Git revision.
Policy binding требует target-local ancestry policy_sha → base_sha; подставить
SHA другого repository нельзя. Gate содержит product package default и dynamic
import probe. Shared conftest и genuine factories смешивают engine lifecycle
с product source/metadata/SMOKE. Отсутствие прямого AST product import не является
доказательством самостоятельности engine.

Источники: [installer](https://github.com/MRDK80/privacy-gateway/blob/27c8d2b458712c44b54d5fedd2ca78c398b6246e/tools/agent_epic_install.py),
[policy binding](https://github.com/MRDK80/privacy-gateway/blob/27c8d2b458712c44b54d5fedd2ca78c398b6246e/tools/agent_epic_policy_binding.py),
[gate](https://github.com/MRDK80/privacy-gateway/blob/27c8d2b458712c44b54d5fedd2ca78c398b6246e/tools/agent_gate.py),
[factory](https://github.com/MRDK80/privacy-gateway/blob/27c8d2b458712c44b54d5fedd2ca78c398b6246e/tests/learning_session_factory_fixture.py).

## Принятое направление B

Разделить immutable engine provenance/installation и target repository policy,
profile и authority конкретного run. Target policy ancestry остаётся внутри
target Git graph. Engine digest доказывает bytes, а не owner approval.
Новый versioned opt-in контракт не переопределяет policy_sha старых configs.
Target profile не является executable grant. Все identity/source checks и
fresh STOP/revoke/expiry/UNKNOWN guards сохраняются перед side effect.

Сохранение старой PGW installation допустимо как временный fallback, но не
считается завершённым выделением. Встраивание расходящихся engine copies в
каждый target ради ancestry не выбрано как целевая архитектура.

## Семантический manifest и контракты

- [Все 511 исходных blobs](392-extraction-manifest.json): source-pinned paths/modes,
  disposition каждого файла. Это proposed semantic selection, не готовый bundle.
- [Девять смешанных и 24 проектных файла](392-extraction-sections.md): конкретное
  распределение sections, сохранение PGW integration и session fixture lifetime.
- [Trust binding](392-extraction-trust-binding.md): четыре группы identity,
  version/legacy strategy и отрицательные contract cases.
- [Resource/process routes](392-extraction-resource-routes.md): источники schemas,
  generated workers, target Git objects, package probes и private run artifacts.
- [Все 43 открытые issues](392-extraction-issue-allocation.md): проектное ownership,
  split #304/#360, history и сохранённые approvals. Это snapshot предложения;
  применять перенос только после fresh state/structural/dependency reconciliation.
- [Demo/rollback](392-extraction-demo-plan.md): entry, synthetic inputs, observable
  trace/oracles, fault matrix, конечный admission и запрет production inference.

## Неизменяемые границы

Старые runs/approvals/ledger/counters/deadlines сохраняют исходную identity.
Автоматическая миграция active runs, перенос grants и обнуление USED запрещены.
Неподдерживаемая version/source даёт fail closed. Перед cutover старый writer
должен быть terminal/STOP и pending effects reconciled. Rollback не переписывает
новый state старым и не запускает двух writers.

Нет экспорта private state/payload, PII, credentials/key material, manifest/route
или sensitive logs. Executor не получает Git/GitHub write; controller независим,
effects выполняет trusted driver с непосредственно применимыми проверками.
Privacy Gateway product guarantees не усиливаются новым именем проекта.

## Последовательность и приёмка

E1 принимает design/semantic selection; bounded contract specification с exact
JSON keys/version и tests согласуется до production-кода нового binding.
E2 создаёт проект/package candidate; E3 переносит ядро после characterization;
E4 выделяет profile и двух consumers; E5 доказывает actual install/recovery/
wakeup/parity/rollback; E6 применяет cross-project issue reconciliation.

Фактические byte bundle и dynamic runtime closure — результаты E2/E3/E4,
а не заявленное доказательство от статического E1 анализа. Старые PGW genuine
tests сохраняются; tiny synthetic engine smoke не заменяет product evidence.
Каждый causal PR проходит полный local gate, независимого controller и exact
PR/actual post-merge CI по policy своего проекта. #383 gate не проверяет E1.

Предлагаемая исходная support policy: один явно pinned engine/profile pair;
прочие combinations UNSUPPORTED до отдельной проверки. Массовый rename не
смешивается с extraction. Старый engine удаляется из PGW отдельным PR после
parity и принятого rollback. MIT notices и исторические source links сохраняются.

Финальный концептуальный пакет согласован владельцем. Для E1 TASK DONE нужна
нормальная документационная доставка после всех gates. Реальная installation/launch, Telegram recipient,
production pilot, publishing и epic→main merge требуют отдельных решений.
