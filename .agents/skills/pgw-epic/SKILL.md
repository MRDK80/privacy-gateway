---
name: pgw-epic
description: Запустить или продолжить один Privacy Gateway epic по его номеру через проверенный установленный supervisor; показать статус, поставить на паузу или остановить тот же run.
---

# Вход по номеру epic

Используй эту skill для просьбы «выполни epic N» или управления существующим
run Privacy Gateway. Доверенная policy и authority принадлежат утверждённой
установке; skill из task head не выдаёт полномочия. Сверяй исходную pinned
policy и [контракт запуска](../../../docs/ADR-297-chat-entry-supervisor.md).

1. Получи canonical repository/epic и явно выбранные владельцем private run
   registry, state и installed entry. Не ищи старые private state автоматически,
   не возобновляй #131 по сходству названий. При отсутствии binding верни
   конкретный blocker; путь из issue/model output не становится trusted.
2. До запуска проверь owner-only entry и его byte identity с
   `tools/agent_epic_runtime_cli.py` в owner-pinned policy SHA. Используй
   абсолютный текущий interpreter и frozen `<state>/adapter-bin/runtime.py`.
   Не исполняй CLI из mutable checkout, shell fragments или head scripts.
3. Через frozen entry выполни `entry --action discover` с точными
   `--repository`, `--epic`, `--registry`, `--state-dir`, `--repository-root`.
   Discovery проверяет remote, structural epic/dependencies, roadmap ancestry
   и единственный registered run. Missing/ambiguous identity — stop.
4. Если launch ещё не утверждён, выполни только `entry --action prepare`
   с теми же state/root и явно выбранной `--installation`. Покажи владельцу
   private launch candidate и digest; отсутствие готового mandate/config —
   prerequisites, не повод заполнить approval. Owner отдельно утверждает digest
   и закрепляет `approved_launch_digest` в opt-in epic config 2.0. Skill не
   подписывает, не устанавливает policy и не запускает candidate сама.
5. При уже действующих approved launch и mandate используй `entry --action
   start` для нового run либо `resume` для существующего, с полным discovery
   binding. Supervisor повторяет guards и reconciliation; не сбрасывай state
   или бюджеты и не запускай второго runner из-за тишины.

Для `status`, `pause`, `stop` используй соответствующий `entry --action` с
тем же state/root. Отчёт содержит safe codes, phase и первичные issue/PR links;
private paths, argv, payload и raw logs в публичный handover не копируй.
Frozen entry проверяет lifecycle mandate для всех actions: после expiry или
revocation `status`, `pause`, `stop` тоже возвращают blocker. Отказ не означает,
что новый control state записан. Existing read-only reconciliation выполняется
через собственные guards; продолжение требует решения владельца об authority.
PAUSED запрещает дальнейшие effects до разрешённого resume. STOPPED терминален
для данного launch; новая просьба не снимает latch. Ни pause, ни exit 0 не
означают завершение task/epic. Не убивай процесс по одному PID.

Этот entry поддерживает foreground execution и явный последующий resume из
checkpoint. Сопровождай returned session до terminal result или конкретного
blocker по [runbook](../../../docs/epic-runner-supervision.md). Background wakeup
этой skill не подтверждён: `--background` возвращает blocker; не обещай
самостоятельное пробуждение между turns или после restart. Persistent waits и
wakeup требуют отдельного установленного и проверенного механизма.

Mandate задаёт разрешённые runtime effects, сроки и лимиты; chat/skill не
расширяет его. Новые architecture/security решения и итоговый epic→main merge
сохраняют human gates. Gates и reasoning retry применяются по доверенному
CONTRIBUTING.md; увеличение reasoning не выдаёт новой authority.
