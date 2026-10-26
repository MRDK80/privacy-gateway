# Возобновляемый local gate (#384)

Статус: implementation draft, приёмка и итоговый gate ещё не завершены.
Контракт: [ADR-384](ADR-384-resumable-local-gate.md). Это отдельный opt-in
инструмент оператора; `agent_verify.py` и его schema1.0 остаются прежними.

## Что сохраняется

Reporter pytest передаёт точный node ID и отдельные setup/call/teardown
результаты. Supervisor подтверждает событие только после durable journal commit.
Независимый таймер создаёт snapshot каждые 900 секунд даже при долгом тесте без
вывода, а также до первого запуска, при pause и завершении unit. Опоздание
отмечается; зависание ОС не позволяет обещать hard real-time. Call PASS ещё не
означает reusable PASS: нужны teardown/session finish, exit0, неизменная
identity и проверенная граница процессов.

Сохраняются два последних валидных поколения. После сбоя проверяется вся
journal history, включая записи после snapshot. Старый snapshot не может
скрыть поздний dispatch или ошибку. Повреждённая/неполная committed history,
неизвестный исход unit и неизвестные процессы дают BLOCKED. Journal append
и snapshots ограничены по размеру; исчерпание не включает silent eviction.
Пропавший store не является новым запуском с нулевыми попытками.

## Профиль и независимые группы

До запуска оператор готовит и независимо проверяет exact JSON profile. Его
SHA передаётся отдельным аргументом. Сам hash не доказывает human approval и
не создаёт authority; требуется исходное разрешение владельца и review этих
байтов. Профиль приватный, за пределами source tree, не для GitHub.

Поля profile version1:

- `root`, `python`, `git`: абсолютные разрешённые реальные пути без symlinks;
- `head`, `base`: существующие task/roadmap refs для identity;
- `inputs`: полный проверенный набор внешних toolchain/dependency/plugin/hook
  входов, включая venv config, реальные package/stdlib/binary inputs. Список
  названий и версий пакетов не доказывает замкнутость входов;
- `environment`: закрытая разрешённая проекция окружения. Обязательны
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` и `PYTHONDONTWRITEBYTECODE=1`; нужны PATH,
  HOME и применимые параметры канонического hook/cache окружения. Нельзя
  включать secrets. Loader не наследует всё окружение оператора;
- `plan`: `version:1`, `review` (digest исходного независимого review), точный
  упорядоченный полный `inventory`, `units` (разбиение того же списка) и
  `allowed_skips` (точные разрешённые synthetic IDs). Xfail в V1 не принят;
- `expires_at`: исходный абсолютный Unix срок; `seconds`: исходное finite
  окно выполнения; `max_collections`: заранее разрешённое число полных
  collection checks от1до16, включая checks после resume.

Node IDs проверяются на пригодность сохранения до dispatch: это только
reviewed synthetic labels, без чувствительных parameter values. Hash ID не
является анонимизацией. Не сохраняются raw stdout/stderr, prompts, env,
exception messages или locals. При отказе CLI печатает фиксированный BLOCKED.

По умолчанию manifest должен описывать всю suite как одну unit. Разделение
нужно обосновать source review общих fixtures/conftest/hooks/global состояния,
external ресурсов и subprocess. Нельзя автоматически считать filename границей
независимости. Разделённые sessions должны быть сопоставлены с непрерывным
инструментированным baseline по полному coverage/outcomes, не только counts.
Нельзя допускать escaping descendants/смену session у дочерних процессов:
текущий runner проверяет отсутствие оставшейся process group и EOF reporter,
но это не доказывает отсутствие отделившегося процесса. Неизвестная изоляция
блокирует profile admission. Неизвестная замкнутость input paths также
блокирует reuse; исключённые cache/private directories не могут быть скрытыми
входами тестов. V1 предназначен для Linux/POSIX; неизвестная platform — отказ,
а не portable PASS.

## Операторский запуск

Только после review и явного разрешения exact profile:

```bash
python -m tools.agent_resumable_gate start --profile /tmp/synthetic-gate-profile.json \
  --approved-profile-sha256 APPROVED_SHA --evidence /tmp/synthetic-gate-evidence \
  --pause-after-units 1
python -m tools.agent_resumable_gate resume --profile /tmp/synthetic-gate-profile.json \
  --approved-profile-sha256 APPROVED_SHA --evidence /tmp/synthetic-gate-evidence
```

`--pause-after-units` останавливает перед следующей unit; не замораживает
текущий процесс. Emergency interruption оставляет UNKNOWN до reconciliation;
автоматического retry/refund нет. CLI не выдаёт новые authorizations и не
возобновляет provider/runtime. В V1 исходный absolute expiry и monotonic
execution deadline не продлеваются на паузу; другой boot блокирует resume.
Поэтому сохранённые PASS не обещают продолжение после любого срока.

`PAUSED` не PASS. `AGGREGATE_PASS` — точный составной local result, отдельно от
GitHub exact CI. Ruff/mypy/pre-commit выполняются исходными модулями той же
Python toolchain, их source/environment receipts проверяются отдельно.
Mutating hooks, сменившие candidate, инвалидируют прежние evidence. Не
передавать fabricated OK/пустые команды в agent_verify и не считать его
schema1.0 расширенной. Переход к принятию aggregate требует внедрённой,
проверенной policy, а не только появления CLI.

## #303

Старые 1909 точек finalgate-v4 не содержат точных IDs и не импортируются.
Полезное независимое разбиение нынешней #303 пока не принято. Whole-suite
unit сохранит диагностические результаты, но не позволит пропускать
отдельные nodes при прерывании внутри session. До дорогого #303 запуска
оператор должен показать реальные принятые units и ожидаемый уровень reuse.
Никакой прогон #303 не разрешается этим runbook.


## Аварийное продолжение: отдельное решение владельца

Обычный `resume` не повторяет unresolved intent. После read-only reconciliation
исходных процессов и pending effects требуется отдельное явное действие
владельца для конкретного следующего attempt. Приватный disposition version1
содержит exact genesis/plan/profile/source digests, frontier и sequence,
исходные boot/deadline, scope/index, previous/next attempt, срок и digests
исходного reconciliation evidence и owner authorization. Это человеческий
trust source; инструмент не выдаёт JSON-флаг за вычисленное доказательство
cleanup и не распознаёт подделку тем же OS owner. PID/group — историческое
наблюдение, не вечное ownership proof. Агент не создаёт и не применяет такие
решения автоматически по одобрению самого дизайна.

Только после отдельного owner approval точного disposition и вызова:

```bash
python -m tools.agent_resumable_gate reconcile \
  --profile /tmp/synthetic-gate-profile.json \
  --approved-profile-sha256 APPROVED_PROFILE_SHA \
  --evidence /tmp/synthetic-gate-evidence \
  --disposition /tmp/synthetic-owner-disposition.json \
  --approved-disposition-sha256 APPROVED_DISPOSITION_SHA
```

Расходование решения и резервирование следующей попытки — один durable event
до spawn под тем же writer lock. Crash после consumption не освобождает grant.
Старые PASS и attempts сохраняются. Fresh inventory проверяется заново в
исходном collection budget; bytes/events/history snapshots также не получают
новых лимитов. Более короткий expiry решения ограничивает prerequisite
inventory и retry до terminal. Отдельное inventory-only решение не продлевает
parent reservation; после её expiry такой вложенный путь остаётся BLOCKED.
Новый явный owner retry того же unit/quality может разрешить следующий attempt
при ещё действующем original launch, без продления самого original deadline.

Во время активного CLI оператор может запросить cooperative pause сигналом
SIGUSR1 только после проверки принадлежности PID этому оператору. Сигнал
не утверждает действий и не останавливает deadline: текущая unit завершается
в своём лимите, следующая не запускается. Сохраняются промежуточные snapshots;
для emergency interruption действуют UNKNOWN/reconciliation правила.
Нельзя сигналить PID только по старому номеру или обещать заморозку fixtures.
