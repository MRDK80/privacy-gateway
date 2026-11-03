# E1 / B: границы нового binding

Направление B подтверждено владельцем; этот документ уточняет контракт для
утверждённого концептуального design; executable schema не вводится и старые поля не меняются.

## Четыре независимые группы identity

1. Engine provenance: repository MRDK80/PrivacyAgents, exact source revision,
   manifest revision, inventory digest всех executable/schema/template bytes.
   Byte digest доказывает содержимое, но не owner approval.
2. Installation: owner-approved candidate digest, installed byte identity,
   interpreter/toolchain и поддерживаемый host capability profile. Source tree
   и actual installed bytes сверяются; mutable target checkout не источник engine.
3. Target: repository identity, epic, единственная roadmap, target policy revision,
   current base/head snapshot и target profile digest. Ancestry проверяется между
   target policy и target base в одном target Git graph, а не между engine и target.
4. Authority/run: owner identity, отдельно approved launch/mandate digest,
   operations allowlist, scope, expires/revoked, original started clock и limits,
   original ledger/counters/pending effects. Engine/profile выбор входит в
   утверждаемую identity до запуска и не может меняться resume-командой.

Новый versioned envelope обязан связывать все четыре группы в canonical digest
без необязательного fallback. Точные имена JSON keys и номера runtime config
назначаются в reviewed contract PR; это additive opt-in version, не изменение
семантики policy_sha внутри старого config. Старые versions остаются на старом
проверенном пути; новый envelope со старым executable отклоняется.

## Порядок проверки

Сначала доверенный локальный owner context, shape/version/unknown fields,
expiration/revocation; затем immutable engine/installation source validation,
target identity/policy ancestry/profile, authority digest и pending-effects
reconciliation. Перед каждым effect повторить свежие clock/STOP/identity/refs,
original limits и применимые gates. Ни один предыдущий READY/digest/controller
PASS не заменяет непосредственно применимую проверку источников.
Неизвестный источник, неподтверждённая ancestry или поддержка версии — fail closed.

## Profile как данные, не executable approval

Target profile ограничен заранее проверенной формой: policy/protected paths,
package/import-root probe, gate profile identifier/explicit argv и CI source
identities, safe report metadata. Его нельзя получить из issue/body/head и
исполнить без проверки. Profile не добавляет operations и не ослабляет engine
protected resources. PGW profile сохраняет нынешние четыре команды и five exact
CI checks; другой consumer получает отдельно reviewed profile. Новые команды,
источники или scopes требуют соответствующего owner решения.

## Migration и compatibility

Прежние approvals остаются привязаны к прежнему repository/policy/config.
Нельзя транслировать их в new envelope по совпадению digest/name. Подготовка
new candidate — не установка, установка — не launch. Новый target/engine pair
требует новых exact approvals; budget history учитывается по исходным grants,
никакого refund/rebase сроков. Historical viewer не запускает actions.
Один поддерживаемый pinned pair — предлагаемая первоначальная support policy;
старую installation удалять только после принятого cutover/rollback.

## Негативные contract cases до production implementation

Каждая группа подменяется отдельно: engine repo/revision/byte, installation,
target repo/epic/roadmap/policy/base, profile, owner/launch/mandate, deadline/counter.
Проверить unknown/duplicate fields, несовместимую version, digest mismatch,
symlink/namespace escape, stale refs, revoked/expired approval, injected issue
authority, неподтверждённый UNKNOWN outcome. Во всех случаях запрет следующего
effect и отсутствие переписывания legacy records. Characterization старых
config versions фиксируется до additive integration; review требует композиции,
а не только теста serializer нового envelope.

Окончательная schema/API spec и regression tests относятся к отдельной bounded
contract task после E1 design acceptance, до production-кода потребителя binding.
Если такой spec расходится с этими invariants — вернуть решение владельцу.
