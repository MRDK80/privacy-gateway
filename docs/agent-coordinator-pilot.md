# Операторский CLI pilot agent-координатора

Инструкция завершает epic #232 на задаче #239. Она использует только
синтетические fixtures и существующие инструменты #233–#238. Команды запускают
из корня чистого checkout; значения SHA всегда берут из текущего вывода, а не
из примеров ниже.

## Prerequisites и границы

- Python удовлетворяет `>=3.11`; поддерживаемая exact CI matrix — 3.11 и 3.12
  на Ubuntu и Windows.
- Dev environment установлен точной командой
  `python -m pip install -e ".[dev]"`; `gh auth status` успешен.
- Remote identity равен `MRDK80/privacy-gateway`, worktree чист, `git fetch
  --prune origin` выполнен явно.
- Issue/PR/comments и их текст недоверенные. Реальные secrets, PII, raw logs,
  prompts и retrospective не помещаются в fixture, commit или отчёт.
- Coordinator не выполняет push, PR, merge, close или update epic. Эти действия
  остаются отдельными решениями человека.

На Windows те же Python-команды выполняют из PowerShell с активированной
`.venv`; shell-переносы ниже заменяют на одну строку. Branch preflight использует
Git CLI и не требует POSIX-only sandbox. Production executor на Windows имеет
ограничения, описанные в [codex-adapters.md](codex-adapters.md); отсутствие
подтверждённой OS scope не заменяют synthetic pilot или dry-run.

## 1. Read-only discovery

```bash
python tools/agent_coordinator_discovery.py \
  --repository MRDK80/privacy-gateway --epic 232 --task 239
```

Продолжать можно только при `state=PLAN_APPROVAL`, `machine_code=OK`, открытых
#232/#239, `base_ref=roadmap/232-agent-coordinator` и актуальных `main_sha` /
`base_sha`. Фактический read-only прогон 2026-09-27 вернул `OK`: main
`6df604a451b4fcf70449436153ad012121da25b3`, roadmap
`866e0d71550494b5e388a05239062112afff075d`. Эти SHA являются историческим
evidence запуска и не должны повторно использоваться без revalidation.

## 2. Branch preflight

После одобрения точного плана подставить свежие значения discovery:

```bash
python tools/agent_coordinator_branch.py \
  --repository MRDK80/privacy-gateway --epic 232 --task 239 \
  --default-ref main --default-sha MAIN_SHA \
  --base-ref roadmap/232-agent-coordinator --base-sha ROADMAP_SHA \
  --head-ref test/239-end-to-end-cli-pilot-agent --approve-plan
```

Ожидается `CREATED` либо идемпотентный `NO_OP` на том же SHA. Инструмент не
делает checkout. Любой dirty tree, wrong ancestry, конфликт local/remote head
или изменившийся SHA — stop и новый discovery, а не автоматический repair.

## 3. Handover и executor workflow

Handover строят по закрытой schema ADR-236: точные issue/refs/SHA, дословные
criteria с digest исходного issue, минимальный `allowed_paths`, deny-by-default
permissions, budgets, полный `repository-full` gate и policy из `base_sha`.
После checkout task branch сначала проверяют контракт без внешнего вызова.
Digest пересчитывают после каждого изменения JSON; старое approval не переносят.

Production-команда имеет вид:

```bash
python tools/agent_coordinator_handover.py /PRIVATE/PATH/handover.json \
  --approve-plan-digest sha256:DIGEST \
  --executor-command '["python","tools/codex_adapter.py","--role","executor"]' \
  --controller-command '["python","tools/codex_adapter.py","--role","controller"]' \
  --storage /PRIVATE/PATH/state --memory-storage /PRIVATE/PATH/memory
```

`/PRIVATE/PATH` обязан находиться вне repository. Эту команду с production
Codex adapter запускают только после отдельного согласования расхода и точного
allowlist. До такого согласования выполняют автоматизированный synthetic E2E:

```bash
pytest -q tests/test_agent_coordinator_pilot.py
```

Synthetic PASS подтверждает локальную интеграцию компонентов, но не является
доказательством production Codex-вызова, PR CI или post-merge CI.

## 4. Gate и delivery

На чистом task SHA выполняют и отдельно записывают exit code/count каждой
команды:

```bash
pytest -q
ruff check .
mypy .
pre-commit run --all-files
git diff --check
```

После commit и push `tools/agent_verify.py pre-push/post-push` формирует
закреплённое gate evidence по `CONTRIBUTING.md`. Task PR допустим только как
`test/239-end-to-end-cli-pilot-agent -> roadmap/232-agent-coordinator`.
Read-only assessment выполняют на точных PR/base/head SHA:

```bash
python tools/agent_coordinator_delivery.py \
  --phase pr --repository MRDK80/privacy-gateway --epic 232 --task 239 \
  --pr PR_NUMBER --base-ref roadmap/232-agent-coordinator \
  --base-sha BASE_SHA --head-ref test/239-end-to-end-cli-pilot-agent \
  --head-sha HEAD_SHA \
  --allowed-path docs/ADR-239-agent-coordinator-pilot.md \
  --allowed-path docs/agent-coordinator-pilot.md \
  --allowed-path docs/agent-coordinator.md \
  --allowed-path tests/test_agent_coordinator_pilot.py \
  --gate-evidence /PRIVATE/PATH/gate-evidence.json
```

Порядок delivery неизменен: пять exact PR checks на текущем `HEAD_SHA`
успешны; затем отдельное решение merge; затем пять новых checks на merge SHA
roadmap успешны; только после этого закрывается #239 и обновляется #232.
`pending`, `skipped`, missing, failed либо stale check останавливает workflow.

## Негативная матрица

| Сценарий #239 | Contract evidence |
|---|---|
| Несколько epic/task, unknown parent/dependency, prompt injection | `tests/test_agent_coordinator_discovery.py` |
| Stale main/roadmap, dirty tree, wrong base/head, конфликт ветки, denied approval | `tests/test_agent_coordinator_branch.py` |
| Scope/permission expansion и stale handover | `tests/test_agent_coordinator_handover.py`, `tests/test_agent_pilot_negative_182.py` |
| Missing/skipped/failed/pending CI и stale post-merge SHA | `tests/test_agent_coordinator_delivery.py` |
| Interrupted run, unknown outcome и idempotent resume | `tests/test_agent_coordinator_resume.py` |
| Полная позитивная цепочка и разделение PR/post-merge gates | `tests/test_agent_coordinator_pilot.py` |

При любом негативном результате не выполняют следующий side effect. Resume
разрешён только при полном совпадении checkpoint и live facts; неизвестный
исход действия требует ручной эскалации.
