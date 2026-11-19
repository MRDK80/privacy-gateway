# Установка CLI и Library API

Поддерживаются Python 3.11 и 3.12 на Linux и Windows. Проверьте версию
выбранного интерпретатора; `requires-python >=3.11` не расширяет tested matrix.
Команды ниже устанавливают конкретный исходный commit, а не editable checkout.
Git должен быть доступен установщику. Наличие нашего релиза на PyPI пока не
подтверждено: не заменяйте source URL случайным одноимённым пакетом из index.
После отдельно подтверждённой публикации installation spec можно заменить
проверенным `privacy-gateway==VERSION` из release evidence.

Для CLI выберите pipx: его отдельное окружение не делает Library API доступным
вашему Python-проекту. Для Library API используйте отдельный venv с pip.
Установка требует чтения GitHub и package index; не отключайте TLS и не
используйте sudo. Диагностика doctor не отправляет внешних сетевых запросов.

## CLI через pipx

Bootstrap venv содержит только установщик pipx; приложение устанавливается в
собственное pipx-окружение. Глобальный Python и shell profile не меняются.
В примерах путь к `pgw` вычисляется через pipx, поэтому перезапуск shell и
`ensurepath` не требуются. Если pipx уже установлен, можно использовать его
вместо bootstrap venv. См. [официальную инструкцию pipx](https://pipx.pypa.io/stable/how-to/install-pipx.html).

### Linux (bash)

Откройте пустую рабочую папку. `PGW_PYTHON` позволяет выбрать установленный
Python 3.11 вместо используемого по умолчанию 3.12.

<!-- pgw-install-smoke: cli-linux -->
```bash
set -e
Python="${PGW_PYTHON:-python3.12}"
Source="git+https://github.com/MRDK80/privacy-gateway.git@dd0f28ffb37a95f20e92f73df44047527e333843"
"$Python" -m venv .pipx-tools
PipxPython="$PWD/.pipx-tools/bin/python"
"$PipxPython" -m pip install pipx
"$PipxPython" -m pipx install --python "$Python" "$Source"
PipxBin="$("$PipxPython" -m pipx environment --value PIPX_BIN_DIR)"
Pgw="$PipxBin/pgw"
"$PipxPython" -m pipx list --short
"$Pgw" --help
"$Pgw" --describe
"$Pgw" doctor
```

### Windows PowerShell

Выберите установленный Python 3.11/3.12 командой `python --version` или задайте
`$env:PGW_PYTHON` его путём. Активация и изменение ExecutionPolicy не нужны.

<!-- pgw-install-smoke: cli-windows -->
```powershell
$ErrorActionPreference = 'Stop'
$Python = if ($env:PGW_PYTHON) { $env:PGW_PYTHON } else { 'python' }
$Source = 'git+https://github.com/MRDK80/privacy-gateway.git@dd0f28ffb37a95f20e92f73df44047527e333843'
& $Python -m venv .pipx-tools
if ($LASTEXITCODE -ne 0) { throw 'venv failed' }
$PipxPython = Join-Path $PWD '.pipx-tools\Scripts\python.exe'
& $PipxPython -m pip install pipx
if ($LASTEXITCODE -ne 0) { throw 'pipx bootstrap failed' }
& $PipxPython -m pipx install --python $Python $Source
if ($LASTEXITCODE -ne 0) { throw 'pipx install failed' }
$PipxBin = & $PipxPython -m pipx environment --value PIPX_BIN_DIR
if ($LASTEXITCODE -ne 0) { throw 'pipx path failed' }
$Pgw = Join-Path $PipxBin 'pgw.exe'
& $PipxPython -m pipx list --short
if ($LASTEXITCODE -ne 0) { throw 'pipx list failed' }
& $Pgw --help
if ($LASTEXITCODE -ne 0) { throw 'help failed' }
& $Pgw --describe
if ($LASTEXITCODE -ne 0) { throw 'describe failed' }
& $Pgw doctor
if ($LASTEXITCODE -ne 0) { throw 'doctor failed' }
```

## Library API через venv и pip

Откройте отдельную пустую папку для проекта. Версия печатается через
metadata: у `pgw` нет отдельного флага `--version`.

### Linux (bash)

<!-- pgw-install-smoke: library-linux -->
```bash
set -e
Python="${PGW_PYTHON:-python3.12}"
Source="git+https://github.com/MRDK80/privacy-gateway.git@dd0f28ffb37a95f20e92f73df44047527e333843"
"$Python" -m venv .venv
VenvPython="$PWD/.venv/bin/python"
"$VenvPython" -m pip install "$Source"
"$VenvPython" -c 'from importlib.metadata import version; from privacy_gateway import PrivacyGateway; print(version("privacy-gateway"))'
"$VenvPython" -m privacy_gateway --help
"$VenvPython" -m privacy_gateway --describe
"$VenvPython" -B -m privacy_gateway doctor
```

### Windows PowerShell

<!-- pgw-install-smoke: library-windows -->
```powershell
$ErrorActionPreference = 'Stop'
$Python = if ($env:PGW_PYTHON) { $env:PGW_PYTHON } else { 'python' }
$Source = 'git+https://github.com/MRDK80/privacy-gateway.git@dd0f28ffb37a95f20e92f73df44047527e333843'
& $Python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'venv failed' }
$VenvPython = Join-Path $PWD '.venv\Scripts\python.exe'
& $VenvPython -m pip install $Source
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
& $VenvPython -c 'from importlib.metadata import version; from privacy_gateway import PrivacyGateway; print(version("privacy-gateway"))'
if ($LASTEXITCODE -ne 0) { throw 'import failed' }
& $VenvPython -m privacy_gateway --help
if ($LASTEXITCODE -ne 0) { throw 'help failed' }
& $VenvPython -m privacy_gateway --describe
if ($LASTEXITCODE -ne 0) { throw 'describe failed' }
& $VenvPython -B -m privacy_gateway doctor
if ($LASTEXITCODE -ne 0) { throw 'doctor failed' }
```

## Обновление и удаление

Команды ниже используют переменные соответствующего installation-примера.
Для обновления сначала задайте `Source` новым проверенным immutable SHA или
подтверждённой release-версией. Повтор с тем же SHA только переустанавливает
тот же source и не доказывает обновление версии. Uninstall удаляет пакет,
а не конфигурации, ключи или пользовательские файлы. Остальные файлы venv
и bootstrap можно удалить после завершения работы из этих окружений.

### pipx, Linux

<!-- pgw-install-smoke: maintain-cli-linux -->
```bash
set -e
"$PipxPython" -m pipx install --force --python "$Python" "$Source"
"$PipxPython" -m pipx uninstall privacy-gateway
```

### pipx, Windows PowerShell

<!-- pgw-install-smoke: maintain-cli-windows -->
```powershell
& $PipxPython -m pipx install --force --python $Python $Source
if ($LASTEXITCODE -ne 0) { throw 'pipx reinstall failed' }
& $PipxPython -m pipx uninstall privacy-gateway
if ($LASTEXITCODE -ne 0) { throw 'pipx uninstall failed' }
```

### Library, Linux

<!-- pgw-install-smoke: maintain-library-linux -->
```bash
set -e
"$VenvPython" -m pip install --upgrade "$Source"
"$VenvPython" -m pip uninstall --yes privacy-gateway
```

### Library, Windows PowerShell

<!-- pgw-install-smoke: maintain-library-windows -->
```powershell
& $VenvPython -m pip install --upgrade $Source
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed' }
& $VenvPython -m pip uninstall --yes privacy-gateway
if ($LASTEXITCODE -ne 0) { throw 'pip uninstall failed' }
```

## Проверка и troubleshooting

- `pgw --describe` — актуальные команды/параметры; каталог здесь не копируется.
- Doctor печатает закрытый отчёт из [ADR-113](ADR-113-safe-doctor.md). WARNING
  keyring не доказывает ошибку установки: predicate не обещает unlock или
  наличие ключа. Используйте безопасный системный backend по [security guide](SECURITY.md),
  без обхода allowlist. Не прикладывайте конфигурацию, credentials и окружение.
- Read-only относится к диагностическим проверкам, конфигурации и keyring;
  стандартный import-cache CPython может записываться при обычном запуске.
  Для исключения этих записей используйте `-B` перед `-m`, как в venv-примере.
- Если `pgw` не найден, используйте вычисленный путь `Pgw`, а не случайный
  одноимённый executable из другого Python. Для library запускайте именно
  `VenvPython`; pipx не добавляет API в окружение вашего приложения.
- Unsupported Python: установите 3.11/3.12 и создайте новое окружение, не
  меняя системные пакеты. Причина missing-dependency FAILURE выясняется по
  состоянию выбранного окружения; не отключайте TLS или security controls.
- Единственный extra — `dev`, только для разработки по CONTRIBUTING.
  NER/proxy/provider extras отсутствуют: см. [DEPENDENCIES](DEPENDENCIES.md).

После установки — [синтетический quickstart](../examples/01_quickstart.py)
и [examples epic #96](https://github.com/MRDK80/privacy-gateway/issues/96).
Для полного примера checkout examples нужен отдельно: wheel не обещает
установку repository examples. Ключи создаются только вашим явным действием.

## Evidence

Source SHA выше фиксирует принятую реализацию #113. Installation smoke извлекает
эти именованные блоки и исполняет native bash/PowerShell в новых изолированных
каталогах. Installer network разрешена; doctor использует принятую ограниченную
read-only capability-проверку. Отдельный artifact smoke #112 проверяет wheel/sdist
текущего кандидата: source-install smoke их не заменяет. Фактические SHA/jobs и
результаты фиксируются в PR и post-merge CI отдельно, успех не предполагается
до первичного результата.

Smoke ожидает каждый native блок не более 600 секунд, без автоматических
повторов. Cleanup process group/tree выполняется best-effort; это не hard
watchdog или атомарная гарантия PID identity. При timeout сценарий не считается
успешным; `cleanup-status.txt` сохраняет результат. На Windows исход очистки
дерева остаётся непроверенным, особенно если shell уже завершился: требуется
разобрать этот отказ до нового прогона, а не считать его успешным cleanup.
