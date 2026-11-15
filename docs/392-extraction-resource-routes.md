# E1: dynamic/resource/process routes — проект контракта

Локальный статический анализ выявил 1751 callsite baseline с process/resource
операциями. Эта оценка не доказывает исполнение или полноту анализа aliases;
сырые локальные locator records не являются частью публичного контракта.
Ни subprocess, ни candidate modules в рамках design анализа не запускались.

| Route class | Источник после выделения | Binding / отрицательный oracle |
|---|---|---|
| tools imports / schema_validate / repository_map | Exact immutable engine manifest; package init включён | Missing/changed byte/version даёт reject, не import из target checkout |
| docs/schemas и learning catalogs | Только восемь перечисленных engine schemas/catalogs в pinned bundle | Не читать schema из task head; изменённый catalog не становится trusted promotion |
| target AGENTS/CONTRIBUTING/security paths | Exact target policy snapshot/profile | Target ancestry остаётся target-local; head не определяет policy |
| target pyproject/workflows, CI source joins | Exact target Git objects по проверенному head/base + profile check identities | Wrong run/head/workflow/matrix rejects; PR CI отдельно от actual postmerge |
| gate package import probe | Explicit target profile package и snapshot import root | Внешний editable install или другое имя не даёт PASS |
| generated worker/SMOKE/CONFTEST | Manifest отдельных byte strings/templates; engine и PGW variants разделены | После materialization hash against expected template; никаких непроверенных model fragments |
| fixture copytree/copyfile | Закрытый synthetic asset manifest плюс engine bundle, не whole current checkout | Не переносить src/privacy_gateway в generic engine fixture; PGW integration отдельно |
| subprocess argv / installed entry | Owner-pinned interpreter, immutable executable/toolchain и approved finite process spec | Без shell expansion, network/write confinement и process lifetime подтверждаются actual admission, не AST |
| gh/git transport | Driver-only, свежие approved target/refs/allowlist/STOP/clock до intent | UNKNOWN reconciliation до повтора; fake transport evidence не GitHub primary |
| run checkpoints/ledger/receipts/cache | Original private run identity; не product Git и не engine distribution | No discovery by similar names; old approvals/counters не мигрируют автоматически |
| skill/bootstrap/installed paths | Owner-approved installation mapping, свежий explicit target binding | Mutable task skill не выдаёт authority; supported wakeup проверяется фактически |

## Closure plan до E3/E4 acceptance

1. До кода составить exact allowlist new engine bundle из выбранных файлов;
   отдельный список target profile resources и generated templates. Сохранить
   provenance к исходным blobs и approved design revision.
2. В characterization пройти production callsites и разделённые fixtures по
   классам выше; отдельно покрыть пути exception/STOP/recovery/teardown, а не
   только happy path импорта. Dynamic/alias edges дополняют AST locator.
3. Сверить manifests до/после сборки wheel/sdist/candidate и в чистом environment;
   проверить отсутствие скрытого fallback в PGW checkout и private state.
4. Проверить consumers shared fixtures вместе; tiny generic scenario не заменяет
   сохранённый PGW genuine integration. Изменившиеся source/evidence identity
   требуют новых checks по действующей policy, без reuse #383 как E1 proof.

Здесь утверждается дизайн маршрутов и future oracles; фактическая runtime closure
остаётся обязательным результатом implementation/parity, не заявляется сейчас.
