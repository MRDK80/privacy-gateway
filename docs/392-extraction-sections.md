# E1: разделение смешанных файлов — проект

Baseline и hashes — в 392-extraction-manifest.json. Ни один исходный файл пока
не меняется. «Создать» ниже означает будущий causal implementation PR после
утверждения окончательного E1. Исторические PGW originals остаются доступны.

## Девять файлов на границе engine / consumer

| Исходный файл / section | PrivacyAgents | PGW / сохранённая проверка |
|---|---|---|
| tests/conftest.py: completed_learning_readers, sandbox, product fixtures | Session completed_learning_readers с исходными consumers и helper; sandbox копировать только как explicit opt-in helper с теми же ограничениями | generate_key/fernet_key, run_cli/exit_code/input_file и product test lookup sites остаются PGW; sandbox не обещает subprocess isolation |
| tools/agent_gate.py: trusted_snapshot_session, allowlist, runners, parsers, build_evidence, evidence predicates | Перенести с тем же наблюдаемым контрактом; не переименовывать snapshot labels/refs при первом переносе | Consumer profile требует исходные четыре команды |
| tools/agent_gate.py: verify_environment и run_repository_full package default | Проверять явно связанный target profile; отсутствующий package/profile — deny, не default privacy_gateway | Профиль указывает privacy_gateway, package source under exact target snapshot; regression прежнего probe |
| tools/agent_epic_planning.py: protected, workflow.POLICY_FILES | Generic protected-path validation остаётся engine; конкретные target policy paths — pinned profile, дополнительно engine schemas/bundle всегда protected | Product src/API/security policy paths остаются PGW policy; issue/head не может их убрать |
| tools/agent_epic_planning.py: Admission, profile, validate_proposal/review | Engine. Новая target-profile binding проверяется до планирования | PGW criteria mapping и grants не расширяются |
| tools/agent_epic_transport.py: command/json/read_json/effect/reconcile и write callbacks | Engine argv transport, authority/UNKNOWN semantics неизменны | Target identity берётся только из mandate/context, не implicit PGW |
| tools/agent_epic_transport.py: GhTaskTransport.pr_body | Generic safe reporting engine; project metadata/workflow path/check-set из проверенного profile и exact Git objects; поля не опциональны молча | Текущие Python minors/Ubuntu-Windows/five-check reporting остаются PGW profile; отрицательные случаи прежней policy сохраняются |
| tests/learning_session_factory_fixture.py: root/state creation, lifecycle configuration и synthetic role transport | Разделить factory inputs на engine bundle + explicit synthetic target assets; private fresh outputs как прежде | Сохранить исходный сценарий в PGW integration, включая actual tools/workflow/profile expectations |
| tests/learning_session_factory_fixture.py: with_local_gate_sut branch | Отдельный generic consumer package synthetic_consumer, не копия продукта с новым названием | Копирование src/privacy_gateway и product metadata/hooks/SMOKE остаётся PGW integration |
| tests/test_agent_final_learning_lifecycle_genuine.py: SMOKE/CONFTEST и generated worker resources | Общие lifecycle oracles по source conservation/STOP/waits/counters остаются engine; generated code имеет самостоятельный manifest hash | Product imports/version/API assertions SMOKE остаются PGW. Запрещено выдавать generic smoke за доказательство PGW API |
| tests/test_agent_epic_transport.py | Generic transport tests с закрытым synthetic profile; отсутствие/подмена profile — негативные случаи | PGW pyproject/workflow/matrix reporting cases сохраняются как consumer tests |
| tests/test_agent_final_preparation_runtime.py | Generic final owner wait/UNKNOWN/original counters; install fixture использует engine candidate и отдельный target | PGW resource layout и product metadata cases остаются integration |
| tests/test_agent_gate_profile.py | Snapshot/probe/error parser tests с synthetic_consumer, не обязательным product install | Положительные/отрицательные privacy_gateway probe assertions остаются PGW |

Session fixture completed_learning_readers и все её consumers характеризуются
вместе: один producer на worker, прежний scope/order/teardown. Не создавать
независимые копии producer ради распределения tests. Определение новой fixture
не меняет consumed budgets и не разрешает перезапуск старого дорогого сценария.

## 24 проектных файла

| Файл | Отбор / создание в PrivacyAgents | Что остаётся в PGW |
|---|---|---|
| .agents/skills/pgw-epic/SKILL.md | Новый engine entry skill с собственной installed identity и neutral target arguments; имя выбирается E2 | Тонкий PGW consumer entry; исходный skill не активирует новый engine автоматически |
| .github/CODEOWNERS | Создать новый owner map после подтверждения owners нового проекта | Текущий owner map |
| .github/pull_request_template.md | Создать template с actual local/PR/postmerge/controller/demo полями и destination scope | Текущий product template |
| .github/workflows/main-source-guard.yml | Перенести правило same-repository roadmap→main как отдельно reviewed workflow нового проекта | Тот же guard в PGW |
| .github/workflows/pre-commit.yml | Новый workflow с закреплёнными engine hooks/toolchain и exact check identity | Текущий workflow и primary CI history |
| .github/workflows/tests.yml | Новый engine suite плюс synthetic consumer; фактическая host-support matrix, без фиктивной universal Windows readiness | Product suite и отдельные PGW adapter integration cases |
| .gitignore | Создать для engine artifacts/private state/venvs; не использовать ignore как security barrier | Текущий product ignore |
| .pre-commit-config.yaml | Сохранить необходимые security/style hooks, re-pin review отдельно; без копирования product baseline approval | Текущие hooks |
| .secrets.baseline | Новая reviewed engine baseline только из разрешённого tree; старую не копировать как approval | Текущий PGW baseline |
| AGENTS.md | Перенести общие development/authority/gate правила с отдельной применимостью; написать engine trust boundary, не PII-продукт | Product boundary, CLI/Library/security invariants и PGW profile |
| CONTRIBUTING.md | Process sections о causal PR, gate/controller/exactCI, retries, supervision, standing approval — адаптировать к новому project workflow; authority не создаётся текстом | Product setup, commands и canonical current process |
| LICENSE | Сохранить MIT copyright/license notice в переносимом коде и новом distribution | Исходная лицензия и notices |
| README.md | Новый README PrivacyAgents: scope, supported host, install status и trust limitations без production readiness claim | PGW product README |
| SECURITY.md | Новый engine reporting policy и security scope | Product security reporting policy |
| docs/ARCHITECTURE.md | Новый engine architecture: identities, installed source, target profile, roles/delivery/recovery; ссылки на исторические ADR | Product architecture; минимальная integration boundary |
| docs/DECISIONS.md | Новый index только применимых engine ADR с PGW provenance links | Полная история PGW decisions |
| docs/GITHUB_RULESETS.md | Новый документ намерений; реальные settings не менять этим scope | PGW settings/runbook, без заявлений о наследовании settings |
| docs/SECURITY.md | Новый engine threat model; issue/head untrusted, no writes for roles, owner-installed trust; same-account compromise limitation | Product guarantees и запрет экспорта sensitive payload |
| pyproject.toml | Новые package/version/entrypoints; engine deps по audit (PyYAML отдельно), собственные dev gate; не копировать cryptography/keyring или pgw script | Product package, version, extras и pgw entrypoint |
| tests/test_check_secrets_baseline.py | Новые engine tests baseline tool/config на synthetic fixtures | Текущая PGW baseline regression |
| tests/test_development_dependencies.py | Engine-specific dependency/build metadata checks | Product dependency contract tests |
| tests/test_package_license.py | License inclusion в новом wheel/sdist | Product distribution checks |
| tools/check_secrets_baseline.py | Общую проверку baseline можно перенести с explicit target/config inputs; generic tests отдельно | Product-specific config invocation остаётся PGW |
| tools/verify_package_build.py | Общий artifact inspector выделять только при доказанной необходимости; по умолчанию новый маленький engine packaging check | PGW package names/layout checks целиком сохраняются |

Эта таблица выбирает semantic ownership sections. Она не обещает byte-identical
результат разделения и не заменяет regression-first implementation. Exact names
новых helpers/package и schema fields фиксируются в соответствующих bounded PR;
если их изменение создаёт новый public/trust contract — вернуться к owner review.

## Остальные 28 файлов

Остаются в PGW: product examples/configs, CLI ADR-150/151, product ADR-45/64/81/86,
Library API, detection/token/threat model, CHANGELOG, env example и product detect
test. Четыре tests/fixtures/synthetic resources не копируются в engine по умолчанию:
нуждающийся consumer должен войти в reviewed fixture manifest. Engine synthetic
consumer создаёт собственные разрешённые данные. Нет «автоматического удаления»
оставшихся docs при переносе копии исторического engine ADR.
