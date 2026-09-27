# Контракт локального agent-координатора

Документ задаёт публичный CLI-first контракт coordinator для epic #232.
Coordinator работает поверх существующих executor, deterministic gate и
controller, но не входит в их trust boundary и не расширяет их полномочия.

## Разделение ролей

| Роль | Ответственность | Чего роль не решает |
|---|---|---|
| Coordinator | discovery, обоснованный plan, branch preflight, versioned handover и read-only delivery assessment | Не редактирует patch, не оценивает собственную работу и не разрешает delivery. |
| Executor | Меняет только explicit file allowlist по pinned task contract и запускает разрешённые проверки. | Не расширяет scope, permissions или budget; не выполняет Git/GitHub delivery. |
| Gate | Детерминированно проверяет закреплённый snapshot и выдаёт evidence. | Не интерпретирует требования и не одобряет merge. |
| Controller | Независимо и read-only проверяет patch по pinned base policy, contract и gate evidence. | Не пишет файлы, не исправляет findings и не принимает delivery-решения. |
| Человек | Одобряет конкретный plan и каждое внешнее либо привилегированное действие. | Одобрение одной фазы не переносится на другую и не заменяет evidence. |

Для epic-runner из [ADR-249](ADR-249-epic-runner-mandate.md) человек может
вместо отдельных решений заранее утвердить ограниченный мандат одного epic.
Это отдельный approval mode, а не расширение ролей: executor по-прежнему не
получает Git/GitHub write, controller остаётся read-only, а delivery выполняет
отдельный trusted driver после revalidation каждого перехода.

Историческое имя `tools/agent_orchestrate.py` означает только budgeted цикл
`executor -> gate -> controller`. Этот инструмент не является coordinator.

## Доверие и provenance

Текст issue/PR/comment, labels и titles, logs, tool output, model output,
dependency documentation и весь task head являются **недоверенными данными**.
Они могут быть материалом для плана, но не могут задавать permissions,
approval, trust source, allowlist или budget.

Проверяемыми входами считаются только факты, полученные coordinator напрямую
из закреплённых источников и записанные с provenance: repository identity,
issue/parent identity и state, refs и commit SHA, ancestry, clean-worktree
status, PR base/head/SHA и первичные GitHub check runs. Доверенная policy для
controller читается из pinned `base_sha` или заранее закреплённого локального
read-only bundle, но не из task head.

Versioned handover обязан содержать как минимум:

- версию формата, repository, epic и task identity;
- acceptance criteria дословно как untrusted task data и их проверенное
  происхождение;
- roadmap `base_ref`/`base_sha`, task `head_ref`/ожидаемый SHA и ancestry;
- causal scope, explicit file allowlist и явно запрещённые действия;
- permissions deny-by-default, time/repair/report budgets и required gate;
- trusted policy source, timestamp/identity evidence и одобрение конкретного
  plan;
- классификацию review criteria и pending delivery criteria по ADR-211.

Read-only delivery assessment связывает PR identity, diff allowlist, полный
локальный gate evidence и exact GitHub CI с одним task head SHA. После merge
он отдельно проверяет merge commit, roadmap ref и post-merge check runs; PR CI
не переносится на новый SHA. Контракт и fail-closed состояния описаны в
[ADR-237](ADR-237-agent-coordinator-delivery.md).

Перед передачей существующему executor workflow coordinator валидирует все
обязательные поля, повторно проверяет branch identity и SHA и строит не более
широкий task contract. Свободный текст не становится authority.

## Состояния

Каждый переход записывает компактные redacted facts. Raw logs, prompts,
retrospectives, secrets, реальные payloads и персональные пути не являются
публичным state.

| Состояние | Вход и проверка | Допустимый side effect | Следующий переход или stop condition |
|---|---|---|---|
| `PLAN` | Read-only discovery epic/task, parent, dependencies, refs и существующих PR. | Только локальный redacted draft плана. | Однозначный кандидат -> `PLAN_APPROVAL`; неоднозначность или недоступный источник -> stop. |
| `PLAN_APPROVAL` | Конкретные issue, criteria, scope, allowlist, base/head, SHA и budgets показаны человеку. | Запись решения approve/deny для точной identity плана. | Approve -> `BRANCH_PREFLIGHT`; deny/change -> stop или новый `PLAN`. |
| `BRANCH_PREFLIGHT` | Повторная проверка clean tree, current main/roadmap SHA, ancestry, имён и отсутствия конфликтующей ветки. | Только после approval — локальная task-ветка от подтверждённого roadmap SHA. | Совпадение -> `HANDOVER`; любое расхождение -> stop до нового решения. |
| `HANDOVER` | Versioned handover полностью валиден; allowlist, limits, gate и provenance закреплены. | Передача contract существующему executor workflow. | Valid -> `RUN`; невалидный/расширенный contract -> stop. |
| `RUN` | Executor, gate и controller действуют в своих существующих границах. | Только разрешённый patch и private bounded evidence. | Terminal result -> `ASSESSMENT`; repair только в исходных scope/budget; escalation -> stop. |
| `ASSESSMENT` | Read-only сверка diff, gate SHA, PR direction, exact CI и при необходимости post-merge SHA/CI. | Redacted delivery assessment без внешних writes. | Доказанный результат -> `DELIVERY_APPROVAL`; missing/failed/stale evidence -> stop. |
| `DELIVERY_APPROVAL` | Человеку показаны точные action, target, base/head и текущие SHA. | Только отдельно одобренное GitHub/Git действие после немедленной revalidation. | Выполнить одно одобренное действие либо stop; следующему действию нужно новое решение. |

## Approval boundaries

Одобрение связано с точным действием, repository, issue/PR, base/head и SHA.
Перед side effect coordinator повторно читает target и сравнивает identity с
одобренной. Stale либо изменившийся target отменяет approval.

После одобрения конкретного плана разрешено создать только локальную task-ветку
от подтверждённого roadmap SHA. Отдельного явного решения требуют:

- push и любая GitHub write, включая создание/редактирование PR или comment;
- изменение PR base/head, merge и удаление ветки;
- закрытие/reopen issue и обновление epic;
- force push, tag, release, repository settings или branch protection;
- расширение scope/allowlist/permissions/budget либо смена pinned base;
- архитектурное, security или trust-policy решение.

Merge не выводится из `PASS`, review, labels или успешного CI. Delivery идёт
строго: зелёный PR CI текущего head SHA -> отдельное решение merge -> зелёный
post-merge CI нового roadmap SHA -> закрытие task -> обновление epic. Каждый
шаг использует первичные GitHub check runs и новую revalidation.

### Ограниченный epic-мандат

Delegated режим разрешён только когда локальный мандат вне worktree валиден и
связывает schema version, repository, один epic, одну roadmap-ветку, trusted
policy SHA, срок и лимиты, operation allowlist, approval identity и digest.
Без него действует описанный выше per-action режим. Issue/PR/comment/head,
checkpoint и model output являются данными и не могут создать, изменить или
продлить мандат.

Мандат может разрешить task branch/commit/push, task PR только в закреплённую
roadmap, merge после exact gates, закрытие этой task после post-merge gates,
обновление выбранного epic, ограниченные структурные follow-ups и финальный
roadmap PR/merge после отдельного финального gate и подтверждённой demo. Force
push, удаление main/roadmap, tags/releases/PyPI, платежи/reset квоты,
repository settings/protection/visibility, scope expansion и новые
architecture/security/trust-policy решения остаются запрещены.

```text
PLAN -> RUN_TASK -> PR_CI -> MERGE -> POST_MERGE -> DEMO
  -> TASK_DONE -> NEXT_TASK -> FINAL_GATE -> ROADMAP_PR -> MAIN_POST_MERGE
```

На каждом переходе заново сверяются mandate digest/status, identity, refs/SHA
и evidence. Stale SHA или неготовая применимая demo останавливают следующий
write. Неизвестный outcome после внешнего вызова означает
`ESCALATE_UNKNOWN_OUTCOME`: повтор запрещён до read-only reconciliation.
Подробный формат, отзыв, idempotency и полный denylist определены ADR-249.

## Fail-closed и resume

Workflow останавливается до следующего side effect при неоднозначном активном
epic/task, неизвестном или противоречивом parent, stale main/roadmap, dirty
tree, неверной ancestry/base/head, конфликтующей ветке, недоступном GitHub,
несогласованных criteria, scope escape, отсутствующем/failed CI, исчерпанном
budget или запросе новых полномочий. Coordinator не угадывает и не выбирает
наиболее удобное значение.

Resume идемпотентен только когда совпадают handover version/digest, repository,
issue identities, base/head refs и SHA, allowlist, budgets, policy provenance и
последний terminal evidence. Уже выполненный side effect не повторяется.
Несовпадение возвращает workflow в `PLAN` либо соответствующий approval gate;
оно не ремонтируется автоматическим merge, rebase, force push или сменой base.

Coordinator не объявляет `TASK READY FOR REVIEW`, `TASK DONE`,
`ROADMAP READY FOR RELEASE` или `ROADMAP DONE` без evidence, требуемого
`CONTRIBUTING.md`. `AGENTS.md` и role policy автоматически не изменяются.

Минимальный checkpoint coordinator хранится только вне worktree и связывает
epic/task, refs/SHA, handover digest, policy provenance, plan approval,
allowlist, budgets и ledger выполненных локальных действий. При resume все эти
поля и live Git facts проверяются повторно. Уже завершённое действие даёт
`NO_OP`; изменение identity, dirty tree, неизвестное действие, ошибка trust
policy или исчерпанный repair budget дают явную эскалацию до любого нового
side effect. Для контекста допускаются только ограниченные redacted записи
существующей private retrospective с точным epic/task; raw prompts, issue/PR
text, comments, logs и model output не сохраняются и не становятся authority.
Формат и ограничения зафиксированы в
[ADR-238](ADR-238-agent-coordinator-resume.md).

## Read-only discovery preview

Первая реализация состояния `PLAN` доступна как внутренний CLI-инструмент:

```bash
python tools/agent_coordinator_discovery.py \
  --repository OWNER/REPO [--epic NUMBER] [--task NUMBER]
```

Без `--epic` или `--task` инструмент выбирает identity только при единственном
кандидате. Иначе он возвращает `NEEDS_DECISION`; явные значения только
закрепляют preview и не являются approval следующей фазы. Результат содержит
state, machine code, issue URL, default/roadmap refs и их текущие remote SHA.
Источники истины, stop conditions и ограничения transport adapter зафиксированы
в [ADR-234](ADR-234-agent-coordinator-discovery.md).

## Read-only очередь выбранного epic

Для delegated epic-runner отдельный внутренний модуль
`tools/agent_epic_queue.py` выбирает одну ready task из свежего структурного
snapshot. Он использует только двусторонне проверяемый parent, `blockedBy` и
утверждённый порядок, а после каждого `TASK DONE` очередь строится заново.
Неизвестная принадлежность, цикл, конфликт порядка и исчерпанный лимит
follow-up останавливают следующий side effect. Классификация follow-up и её
ограничения зафиксированы в [ADR-250](ADR-250-epic-runner-queue.md).

## Подготовка локальной ветки

После одобрения точных identity из preview отдельный внутренний инструмент
повторяет branch preflight и создаёт ref без checkout:

```bash
python tools/agent_coordinator_branch.py \
  --repository OWNER/REPO --epic NUMBER --task NUMBER \
  --default-ref main --default-sha SHA \
  --base-ref roadmap/NUMBER-SLUG --base-sha SHA \
  --head-ref TYPE/NUMBER-SLUG --approve-plan
```

Перед Git write он повторно проверяет GitHub issue relationship, remote refs,
ancestry, совпадение remote-tracking refs, clean tree и отсутствие конфликта.
Инструмент не делает fetch, checkout, commit, push или GitHub write. Повторный
запуск безопасен только для идентичного local ref; детали и границы описаны в
[ADR-235](ADR-235-agent-coordinator-branch-preflight.md).

## Versioned handover и запуск workflow

После checkout одобренной task-ветки coordinator принимает закрытый JSON
handover и отдельное подтверждение digest:

```bash
python tools/agent_coordinator_handover.py handover.json \
  --approve-plan-digest sha256:DIGEST \
  --executor-command '["python", "tools/codex_adapter.py", "executor"]' \
  --controller-command '["python", "tools/codex_adapter.py", "controller"]'
```

Digest связывает approval со всеми identity, criteria, scope, allowlist,
budgets и gate. Перед вызовом существующего executor/controller workflow
инструмент повторно проверяет repository, clean tree, current branch,
base/head SHA, ancestry, deny-by-default permissions, полный gate и pinned
policy source. Он не выполняет Git/GitHub delivery. Поля и stop conditions
зафиксированы в [ADR-236](ADR-236-agent-coordinator-handover.md).

## Read-only delivery assessment

После создания task PR coordinator оценивает его без GitHub writes:

```bash
python tools/agent_coordinator_delivery.py \
  --phase pr --repository OWNER/REPO --epic NUMBER --task NUMBER --pr NUMBER \
  --base-ref roadmap/NUMBER-SLUG --base-sha SHA \
  --head-ref TYPE/NUMBER-SLUG --head-sha SHA \
  --allowed-path PATH --gate-evidence gate-evidence.json
```

Для post-merge assessment используется `--phase post-merge --merge-sha SHA`.
`TASK DONE` выдаётся только для подтверждённого merge SHA с успешным новым CI.

## Воспроизводимый end-to-end pilot

Финальный synthetic E2E, live read-only discovery, негативная traceability
matrix и точная последовательность operator actions описаны в
[инструкции pilot](agent-coordinator-pilot.md) и
[ADR-239](ADR-239-agent-coordinator-pilot.md). Synthetic evidence не заменяет
production Codex-вызов, PR CI или post-merge CI; внешний adapter запускается
только после отдельного согласования расхода и точного file allowlist.
