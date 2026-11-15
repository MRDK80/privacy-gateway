# E1: observable demo и rollback — проект до реализации

Статус: концептуальный план утверждён владельцем вместе с ADR. Здесь ничего не запускается. Это сценарий
будущей E5, не доказательство текущей работоспособности и не production pilot.

## Вход и ограничения

Два новых изолированных synthetic repositories: маленький generic consumer с
пакетом synthetic_consumer и PGW integration fixture с синтетическим input.
Отдельные state/installation/ledger на каждый run; никаких существующих private
runs, credentials, реального keyring/PII. Exact source revisions, engine allowlist,
bundle digest, target policy/profile, Python/toolchain, host namespace и resources
фиксируются заранее. Candidate НЕ импортируется до принятой installation проверки.
Owner отдельно утверждает finite work/cleanup/outer, call/task/write budgets,
allowlist и STOP mechanism; числовые лимиты сейчас не выдумываются.

## Entry и путь

Будущий pinned installed entry (его имя решается E2), не mutable checkout script:
read-only readiness → prepare candidate → отдельное exact owner approval → install
в fresh namespace → synthetic bounded launch двух задач → executor/gate/controller
→ fake GitHub PR CI → fake task merge → fake actual postmerge CI → retrospective
→ вторая задача → final candidate → WAITING_OWNER_FINAL_APPROVAL. Без main effect.
Transport по умолчанию локальный fake server/adaptor с проверенным журналом;
ни один fake CI SUCCESS не является GitHub CI. Реальная GitHub delivery acceptance
остаётся отдельным exact PR/postmerge gate проекта, не частью offline demo.

## Наблюдаемые oracles

Для каждого перехода сравниваются ожидаемые phase и safe machine status,
repository/epic/roadmap/source/profile/installation identities, неизменность
прочитанных source bytes, фактические intent/result ledger и счётчики.
Результат не выводится из одного checkpoint. Проверяются exit/child reap,
absence второго writer и отсутствие запрещённых transport calls.
Каждый expected effect имеет ровно один проверенный identity/result; порядок
журнала сравнивается с pre-registered trace. Полные private traces остаются
локальными; публичный отчёт содержит только безопасный итог и primary references.

## Fault matrix и критерии

| Сценарий | Вмешательство | Требуемое наблюдение |
|---|---|---|
| Happy path | Две synthetic tasks | Два отдельных task terminal, полный согласованный gate, final owner wait; ноль main effects |
| Drift | По одному заменить bundle/profile/target/policy/base | Stop до следующего intent/write; ни fallback на похожую identity, ни silent trust |
| STOP/revoke/expiry | Подать после первого checkpoint до следующего effect | Новый effect запрещён; первоначальные clocks/USED сохраняются |
| UNKNOWN | Fake transport применяет effect, затем timeout | После restart read-only reconciliation доказывает APPLIED; effect не повторяется |
| UNKNOWN unresolved | Fake source не позволяет установить исход | Конкретный owner blocker; нет повторной записи/возобновления по checkpoint |
| Restart/quota wait | Остановить процесс в подтверждённой durable wait | Фактический wakeup на поддерживаемом host с той же identity/deadline/counters; иначе UNSUPPORTED/BLOCKED |
| Fixture parity | Generic consumer и PGW integration | Engine работает без установленного privacy_gateway; отдельные PGW product smoke/session lifetime tests сохраняются |
| Bad legacy | Передать старую/неподдерживаемую schema | Fail closed без rewriting/import approval или counter refund |
| Final stop | Не выдавать owner final approval | Persistent wait; main merge и close epic отсутствуют в transport trace |

## Rollback rehearsal

До demo сохранить проверенный старый installation candidate и отдельную synthetic
old-run history. После injected failure нового candidate: STOP нового run,
reap всех принадлежащих ему processes, read-only reconcile UNKNOWN, сохранить
ledger/USED/deadlines. Затем проверить отсутствие active writer и возможность
применения old installation к ЕЁ исходной identity; запускать только по отдельной
действующей synthetic authority. Не переносить новый checkpoint в старую схему.
Oracle: старые bytes/history неизменны, новые failure receipts сохранены, нет
двух writers, повторов applied effects или обнулённых limits. Если old identity
неприменима/expired — rollback считается blocked, а не forced success.

## Приёмка и human decisions

Перед execution независимо проверить full composed scenario, fixture lifetimes,
source/namespace/resource admission и finite budgets. Root и controller оценивают
actual outputs раздельно. Owner принимает observable demo по trace и итогам, а не
synthetic mocks как production proof. Installation/activation реального PGW,
Telegram recipient и production pilot требуют отдельных fresh approvals.
