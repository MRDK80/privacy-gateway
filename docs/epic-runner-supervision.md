# Сопровождение epic runner (#316)

## Причина и граница исправления

В #316 оператор получил running session ID, но завершил turn сообщением о
продолжении runner; наблюдение восстановилось только после вопроса владельца.
Это установленная потеря operator supervision. Причина задержки `RUN_TASK`
и программный дефект runtime не установлены. Поэтому исправление касается
операционного lifecycle, а production runtime и публичные контракты сохраняются.

Этот runbook не обеспечивает фонового исполнения сам по себе. Он задаёт
проверяемые действия и основания остановки. Regression-first test обязателен,
если дальнейшее расследование подтвердит дефект исполняемого компонента;
операционный сценарий ниже не является тестом поведения модели. Нельзя
объявлять runtime исправленным на основании изменения policy-текста.

## Запуск и bounded observation

1. До запуска сверить authority, repository/epic, policy, task/base/head SHA,
   deadline, stop/expiry/revocation и отсутствие уже активного runner. Использовать
   разрешённый frozen entry. Не запускать production runner ради проверки этого
   runbook; synthetic сценарий ниже не читает private epic state.
2. Сохранить в owner-only записи вне worktree: process/session ID, при наличии
   PID и start identity (PID может переиспользоваться), client/host identity,
   task/phase, последнее время наблюдения, отдельно время последнего доказанного
   progress и его основание. Не сохранять payload, argv с private paths, raw
   output или key material. Не добавлять поля в canonical checkpoint/ledger.
3. После возвращённого running session ID продолжать bounded чтения этого же
   session. В текущем tool surface это `exec_command` → `write_stdin(session_id)`;
   для yielded `functions.exec` используется `functions.wait(cell_id)`. Эти IDs
   относятся к разным механизмам и не взаимозаменяемы. Между ожиданиями сообщать
   факты, не трактовать timeout чтения как timeout самого runner.
4. Process alive, новый timestamp наблюдения, heartbeat, шум output и неизменный
   checkpoint `RUNNING` не являются progress. Основание progress — проверенное
   завершение фазы/операции, новое SHA-bound evidence или переход task/phase,
   подтверждённый canonical facts. Для длительного `RUN_TASK` явно писать
   «процесс наблюдается; продвижение с последнего evidence не подтверждено».
5. Соблюдать исходный deadline и authority. Тишина сама по себе не разрешает
   restart, повтор effect или вмешательство. При исчерпании доступного времени
   наблюдения фиксировать конкретную блокировку и владельца следующего действия.
   Не продлевать deadline или мандат. Не останавливать чужой действующий runner.

## Exit, CI_PENDING и решения владельца

Exit code и terminal output процесса проверяются вместе с canonical state.
Exit 0 не доказывает `TASK_DONE`; nonzero не доказывает `NOT_APPLIED`.
При pending effect сначала выполняется read-only reconciliation по ADR-249/284.
`UNKNOWN` запрещает повтор до доказанного outcome и решения применимого guard.

При `CI_PENDING` процесс может уже завершиться: session polling тогда не
заменяет чтение GitHub. Примерно раз в минуту читать primary check runs для
точного head/merge SHA, проверять все пять exact names, status и conclusion.
Отдельно различать PR CI, post-merge roadmap CI и main CI. Pending — ожидание;
failure — конкретный gate blocker. Success только одного run/job недостаточен.
После пяти SUCCESS обновить authority, identity/SHA, stop/expiry/revocation и
pending effects; продолжить только разрешённую фазу и при отсутствии активного
runner. CI success не выдаёт permission на merge/close или новый мандат.

ADR-290 retries одного transport read и периодическое ожидание CI — разные
циклы. Retry не превращает CI_PENDING в успех, не повторяет write и не
продлевает исходный outer deadline, recovery approval или mandate expiry.
Owner stop/cancellation, expiry/revocation запрещают следующие разрешённые
эффекты даже если CI стал зелёным. Само наблюдение не очищает stop latch.

## Turn transition и reconnect

Проверить **три отдельных свойства** текущего клиента: запуск процесса,
чтение/восстановление session и пробуждение нового turn. Наличие CLI или tool
не доказывает все три. На проверенном surface `codex-cli 0.157.1` и доступные
`exec_command`/`write_stdin` позволили сопровождать delayed synthetic process
до exit внутри turn. CLI version не является версией desktop app. В #316
cross-turn wakeup и восстановление session после перезапуска клиента не были
демонстрированы; гарантии фонового наблюдения отсутствуют.

[Official Goals documentation](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)
описывает отдельный механизм continuation с idle/budget/interruption условиями;
[scheduled tasks](https://learn.chatgpt.com/docs/automations?surface=app)
описывают отдельно настроенное исполнение. Ни обычный prompt, ни наличие этих
возможностей не доказывают сохранение конкретной process session. Не создавать
Goal/automation и не передавать другой chat полномочия без соответствующего
разрешения владельца. Если mechanism не настроен и не проверен, наблюдать в
активном turn до terminal result либо конкретной блокировки.

Если разрешён переход, сначала на synthetic fixture проверить его фактическое
пробуждение, получение прежней identity и восстановление read-only наблюдения.
Записать mechanism ID, получателя, evidence последнего успешного wakeup,
границы authority, следующую проверку и fallback при потере связи. Для текущего
runner не обещать восстановление, пока такой probe не прошёл. Final допустим
лишь с проверенным terminal result или конкретным blocker и честным handover.

После reconnect сначала сверить saved identity с реальным process/start
identity и canonical task/phase, checkpoint, ledger, pending effects. При
недоступной session проверить процесс read-only; PID без start identity не
достаточен. Недоступность observation transport не доказывает exit. Если
identity/outcome не подтверждаются, требуется owner decision; не создавать
duplicate runner. Exit с pending effect требует reconciliation, а не restart.
Не редактировать checkpoints, attempts, mandates, receipts или stop latch.

## Проверяемая consumer demo текущего клиента

Это synthetic process demo, не production pilot и не автоматическая проверка
поведения модели. Запустить команду через process tool с начальным bounded
ожиданием около одной секунды. Сохранить returned session ID и дочитать **эту
же** session до exit. Команда не обращается к GitHub или private epic state.

```bash
python -u - <<'PYEOF'
import json
import subprocess
import sys
import time

child = subprocess.Popen([
    sys.executable, "-u", "-c",
    "import time; time.sleep(4); print('synthetic phase completed', flush=True); "
    "time.sleep(2)",
], stdout=subprocess.PIPE, text=True)
try:
    print(json.dumps({"observation": "alive", "progress": "unconfirmed"}), flush=True)
    # One-second silence is an observation, not proof of phase completion.
    time.sleep(1)
    assert child.poll() is None
    print(json.dumps({"observation": "alive", "progress": "unconfirmed"}), flush=True)
    output, _ = child.communicate(timeout=10)
    assert output.strip() == "synthetic phase completed"
    print(json.dumps({"observation": "synthetic phase evidence", "progress": "confirmed"}), flush=True)
    assert child.returncode == 0
    print(json.dumps({"observation": "exit 0", "result": "synthetic terminal"}), flush=True)
finally:
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
PYEOF
```

Старое ошибочное действие: после returned session ID произнести final
«runner продолжает работу» без следующего чтения. Оно **не проходит** сценарий:
terminal output/exit не наблюдались, wakeup не доказан. Новый протокол проходит
только после сохранения identity, наблюдения обоих alive/unconfirmed событий,
phase evidence и exit. Synthetic phase line здесь — заданный fixture evidence;
произвольная строка production output не доказывает phase progress.

## Failure-path checklist

Каждая строка требует записи фактического наблюдения и outcome; пропущенное
испытание отмечать «не проверено», а не PASS. Эти сценарии проверяют операторский
протокол; существующие runtime regression tests отдельно проверяют authority и
reconciliation. Не запускать дополнительный #131 runner ради checklist.

| Сценарий | Ожидаемый результат и evidence |
| --- | --- |
| Delayed output | Та же session прочитана до exit; тишина не названа progress. |
| Долгая фаза | Observation обновляется, last proved progress остаётся прежним; исходный deadline сохраняется. |
| Exit без completion | Сверены output/exit/state; `TASK_DONE` не выводится из exit 0. |
| CI_PENDING → green | Читаются primary facts раз в минуту; пять exact checks привязаны к нужному SHA; перед разрешённым resume повторены guards. |
| Reconnect | Process/start и state identities сверены read-only; нет второго runner или повторного effect. |
| Owner stop, expiry/revoke во время wait | Дальнейшие effects запрещены; success read/CI не открывает authority. |
| UNKNOWN write | Только read-only reconciliation; нет фиктивного receipt или blind retry. |
| Turn transition | Есть фактический synthetic wakeup и восстановление identity либо явно «не поддерживается/не проверено», без обещания фона. |

## Reflection, handover и применение к #131

После `TASK_DONE` сохранить только фактический redacted урок, provenance и
критерий применимости по ADR-259; не устанавливать новую trusted policy.
Handover любой остановки содержит main/roadmap/base/head SHA, process/session
identity и последнее observation/progress evidence в private части, terminal
или blocker, pending/reconciliation, отдельные local/CI результаты, review,
проверенные и непроверенные свойства клиента, следующий допустимый шаг.
Public handover не содержит private paths, raw logs, payload или state files.

Для #131 сначала пройти task→roadmap→main и соответствующие gates. Затем
отдельно согласовать применение runbook и, если требуется, policy installation,
mandate/digest и state transition. Действующий runner не заменять и не
останавливать в рамках #316. Перед любой будущей командой сначала read-only
reconnect/reconciliation. Merge этого PR не активирует ничего в #131 и не
разрешает reset private state или второй runner.
