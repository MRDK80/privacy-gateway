# Installing the CLI and library

[English](INSTALLATION.md) | [Русский](INSTALLATION.ru.md)

Python 3.11 and 3.12 are supported on Linux and Windows. Check the selected
interpreter; `requires-python >=3.11` does not expand the tested matrix.
These commands install a specific source commit, not an editable checkout.
Git must be available to the installer. A Privacy Gateway PyPI release has not
been confirmed: do not substitute an arbitrary same-named index package.
Only after separately verified publication may the source specification be
replaced by `privacy-gateway==VERSION` from trusted release evidence.

Choose pipx for the CLI: its environment does not expose the library to your
Python project. For the library, use a separate venv and pip. Installation needs
GitHub/package-index reads; do not disable TLS or use sudo. Doctor makes no
external network requests.

## CLI with pipx

The bootstrap venv holds pipx only; the application gets its own pipx environment.
Global Python and shell profiles are unchanged. Paths are obtained from pipx,
so no shell restart or `ensurepath` is needed. An existing pipx installation can
replace the bootstrap venv. See the [official pipx guide](https://pipx.pypa.io/stable/how-to/install-pipx.html).

### Linux (bash)

Start in an empty directory. `PGW_PYTHON` selects an installed 3.11 interpreter
instead of the default 3.12.

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

Check `python --version` or set `$env:PGW_PYTHON` to an installed 3.11/3.12
interpreter. Activation and ExecutionPolicy changes are unnecessary.

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

## Library with venv and pip

Start in a separate empty project directory. Read the version from distribution
metadata: `pgw` has no standalone `--version` flag.

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

## Updating and uninstalling

Use variables from the corresponding installation example. Before updating, set
`Source` to a newly verified immutable SHA or confirmed release version. Repeating
the same SHA reinstalls that source; it does not prove a version upgrade.
Uninstall removes the package, not keys, configuration or user files. Remove the
remaining venv/bootstrap directories only after finishing work in those environments.

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

## Verification and troubleshooting

- `pgw --describe` is the current command/parameter source; this guide does not
  duplicate its catalog.
- Doctor prints the closed report defined by [ADR-113](ADR-113-safe-doctor.md).
  A keyring WARNING need not mean installation failure: capability does not
  promise an unlocked vault or an existing key. Use an allowed system backend
  per the [security guide](SECURITY.md), without bypassing the allowlist. Do not
  attach configuration, credentials or environment dumps.
- Read-only covers diagnostic checks, configuration and keyring. Normal CPython
  imports may write bytecode caches. Use `-B` before `-m` to exclude those writes,
  as shown in the venv example; this is not a zero-process/OS-write guarantee.
- If `pgw` is not found, use the calculated `Pgw` path, not a same-named executable
  from another Python. For the library use `VenvPython`; pipx does not install the
  API into your application's environment.
- For unsupported Python, install 3.11/3.12 and create a new environment rather
  than changing system packages. Investigate missing-dependency FAILURE in the
  selected environment; do not disable TLS or security controls.
- The sole extra is development-only `dev`, per CONTRIBUTING. No NER/proxy/provider
  extras exist; see [dependencies](DEPENDENCIES.md).

Next: the [synthetic quickstart](../examples/01_quickstart.py) and
[examples index](../examples/README.md) from [epic #96](https://github.com/MRDK80/privacy-gateway/issues/96).
Repository examples need a separate checkout; wheel installation does not promise
to install them. Keys are created only by your explicit action.

## Evidence and offline smoke boundary

The pinned SHA contains the accepted #113 implementation. Installation smoke
extracts the named blocks and executes native Bash/PowerShell in new isolated
directories. Installer network access is allowed; doctor uses its accepted limited
read-only capability check. Separate #112 artifact smoke tests the current
candidate's wheel/sdist; source installation is not a substitute. Record actual
SHA/jobs and outcomes separately for PR and post-merge CI.

Package acquisition is not claimed to work offline. Once dependencies are
installed, the separate synthetic quickstart/scenario tests run with networking
and real keyring access forbidden, disposable home/workspace and an in-memory key.
These checks do not establish real vault readiness. See the tests linked in the
[examples index](../examples/README.md).

Each native block has a 600-second wait, no automatic retry. Process-group/tree
cleanup is best effort, not a hard watchdog or atomic PID-identity guarantee.
Timeout is failure; `cleanup-status.txt` records the cleanup outcome. Windows
process-tree cleanup remains unverified, especially after the shell exits:
investigate before another run rather than claiming successful cleanup.
