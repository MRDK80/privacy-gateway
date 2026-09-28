# Epic-runner: operator runbook и production pilot

Эта инструкция применяется только к epic #248 и не является глобальным
разрешением. Перед запуском владелец отдельно утверждает локальный мандат вне
worktree и его digest.

## Зафиксированное окружение pilot

На 2026-09-28 локальная preflight-проверка показала:

- `codex-cli 0.157.1`;
- Python 3.12.3;
- Linux 7.0.0-34-generic x86_64;
- Bubblewrap 0.9.0.

Это наблюдение среды, а не доказательство успешного production-вызова. Перед
фактическим pilot версии записываются повторно. Если Linux Bubblewrap
недоступен, production adapter не запускается и менее строгий fallback
запрещён.

## Запуск и status

Все state/ledger/lesson paths должны находиться в owner-only private directory
вне repository. Одна локальная команда возобновления конкретного checkpoint:

```bash
python tools/agent_epic_loop.py resume \
  --state-dir /PRIVATE/epic-248 \
  --repository-root /PATH/privacy-gateway
```

Без runtime adapters команда закономерно возвращает `ADAPTER_REQUIRED` и не
делает side effect. Текущий безопасный status:

```bash
python tools/agent_epic_loop.py status \
  --state-dir /PRIVATE/epic-248 \
  --repository-root /PATH/privacy-gateway
```

## Закрытая последовательность production pilot

1. Повторно сверить repository identity, epic #248, roadmap ref/SHA, чистое
   дерево, mandate digest/status/expiry/revocation и точный task grant.
2. Записать `codex --version`, `python --version`, `uname -srmo` и
   `bwrap --version`; raw logs не прикладывать.
3. Выполнить один разрешённый production Codex-вызов только с синтетическим
   input и minimal file allowlist. Зафиксировать bounded status/code и версию,
   но не prompt/model output.
4. Выполнить полный local gate и independent controller. GitHub delivery
   разрешён только по отдельным SHA-bound PR/post-merge gates и primary run/job
   IDs.
5. Для финала выполнить подготовленную consumer demo локально на реальном
   input; наружу сообщается только подтверждение владельца, не input/output.

## Pause, revoke, resume и решения оператора

- `PAUSED_RATE_LIMIT`: не повторять фазу и не расходовать reset credit;
  дождаться bounded poll, заново прочитать quota и live identity, затем resume.
- Revoke: запретить все новые side effects. Уже подтверждённые receipts не
  откатывать; новый мандат требует нового digest и явного approval.
- Crash/unknown outcome: сначала read-only reconciliation. Только доказанный
  `NOT_APPLIED` допускает один повтор; `APPLIED` даёт `NO_OP`; `UNKNOWN`
  остаётся `ESCALATE_UNKNOWN_OUTCOME`.
- `NEEDS_DECISION`: остановиться и показать конфликт parent/dependencies/order,
  stale identity или отсутствующий allowlist владельцу. Не выбирать удобное
  значение и не расширять scope.
- CI failure: не merge/close/update; исправление проходит отдельный causal diff
  и полный gate заново.

Synthetic E2E запускается как часть `pytest -q`; он не заменяет перечисленный
production pilot, реальный GitHub CI или подтверждение финальной demo.
