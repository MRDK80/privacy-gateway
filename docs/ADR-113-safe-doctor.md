# ADR-113. Read-only установка и диагностика doctor

Статус — утверждено владельцем 2026-11-19 (#113).

Синтаксис команды публикуется через pgw --describe.
Без PATH проверяются установка и platform default capability keyring. Нет default пути
в checkout. Help/describe получают новую команду через ADR-151.
pgw --json doctor пока возвращает существующий unsupported_command/exit3.
Существующие JSON envelope/schema и команды сохраняют контракты.

## Проверки и вывод

Фиксированный порядок строк PACKAGE, PYTHON, DEPENDENCIES, KEYRING, CONFIG,
OPTIONAL, SUMMARY. Формат каждой строки CHECK: STATUS и завершающий newline.
Статусы OK, WARNING, FAILURE, NOT_REQUESTED, NOT_AVAILABLE.
Свободный текст из exceptions/backend/config/environment не выводится.

- PACKAGE — distribution metadata доступна, версия совпадает с литералом.
- PYTHON — 3.11/3.12 OK; более новая >=3.11 WARNING. Проверка на старых Python ниже install floor не обещается.
- DEPENDENCIES — импортируются cryptography, keyring, yaml; ошибка — FAILURE.
- KEYRING — проверяется только встроенный platform backend из существующего
  allowlist: SecretService на Linux, WinVaultKeyring на Windows.
  Авто-discovery, get_keyring и custom-backend plugins не вызываются.
  Predicate выполняется в отдельном изолированном дочернем Python-процессе,
  один раз с subprocess timeout 5 секунд, без автоматического повтора.
  Parent принимает только закрытые статусы, не экспортирует child diagnostics;
  диагностический вывод ограничен 1024 байтами. При timeout/error WARNING.
  Это конечный deadline ожидания, без обещания hard real-time завершения ОС.
  Используется availability predicate class.priority; backend не
  создаёт коллекций, не разблокирует vault и не вызывает credential methods.
  На Linux допускается только локальный AF_UNIX D-Bus. Фактическая библиотека
  выполняет auth и AddMatch для Prompt interface, затем NameHasOwner и, при
  необходимости, ListActivatableNames. AddMatch — временная подписка до close.
  Запуск prompt, создание коллекции и активация сервиса исключены.
  Допускаются только эти read-only capability queries,
  без активации сервиса, чтения секретов или внешней сети. На Windows проверяется
  штатный WinVault capability predicate. OK при доступном predicate;
  WARNING при недоступном/неподдерживаемом backend. При отсутствии keyring
  NOT_AVAILABLE и DEPENDENCIES FAILURE.
  Проверяется platform default capability, а не пользовательский override.
  Успех не обещает доступ к конкретному vault, наличие ключа или успешный unlock.
  Такая проверка даёт достижимый SUMMARY OK на корректно настроенной установке.
  Backend classname, paths, username и hostname не выводятся.
- CONFIG — с PATH используется существующий detector.load_config:
  YAML должен быть mapping, текущие permissive правила полей сохраняются.
  Строгая JSON schema не обещается. OK/FAILURE; без PATH NOT_REQUESTED.
  При отсутствии PyYAML с PATH NOT_AVAILABLE и DEPENDENCIES FAILURE.
- OPTIONAL — NOT_AVAILABLE, продуктовых extras сейчас нет; NER/SDK не импортируются.
- SUMMARY — FAILURE при обязательном отказе, иначе WARNING при предупреждении,
  иначе OK. CONFIG NOT_REQUESTED и OPTIONAL NOT_AVAILABLE не ухудшают итог.

Exit0 для OK/WARNING, exit3 для обязательной установки/config FAILURE,
exit1 для unexpected_error с фиксированной безопасной строкой.
Используются существующие числовые exit codes. Argparse misuse сохраняет exit3.
stderr пуст для штатного отчёта; unexpected error выводит только безопасную константу.

## Bootstrap граница

Фактический privacy_gateway.__init__ eager-импортирует facade и crypto, а CLI
импортирует обязательные зависимости до dispatch. При отсутствии зависимости
ImportError возникает раньше doctor. Добавления parser command недостаточно.

Приняты lazy public API exports с сохранением __all__, имён и статической
типизации, плюс stdlib bootstrap перед operational CLI. Entry point pgw
направляется в bootstrap; прежние команды делегируются существующему CLI.
Оба пути pgw и python -m privacy_gateway идут через этот bootstrap.
Doctor получает ограниченный диагностический путь. Характеризующие тесты
должны подтвердить прежние stdout/stderr/exit и публичные импорты.

## Граница read-only и bytecode cache

Уточнение утверждено владельцем 2026-11-19 после cold-source regression.
Read-only относится к самим диагностическим проверкам, пользовательским данным,
конфигурации и keyring. Обычный запуск через `pgw doctor` может создать или
обновить стандартный Python `__pycache__` при импорте пакета. Этот кэш не входит
в гарантию отсутствия записей: CPython сохраняет байткод до выполнения кода
модуля, поэтому установка флага внутри bootstrap была бы слишком поздней.

Чтобы исключить именно Python import-cache writes, используйте в выбранном
окружении `python -B -m privacy_gateway doctor`. Флаг применяется интерпретатором
до первого импорта пакета. Это не обещание полного отсутствия любых записей
процесса или операционной системы. Запрет записи диагностикой пользовательских
данных, конфигурации и keyring, чтения credentials и внешней сети сохраняется.
Cold-source tests проверяют оба bootstrap входа и JSON rejection без готового
кэша: с `-B` файлы копии пакета не изменяются; без него допускаются только
дополнительные файлы стандартного import cache.
