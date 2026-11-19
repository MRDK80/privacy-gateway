# Base-installation dependency contract

[English](DEPENDENCIES.md) | [Русский](DEPENDENCIES.ru.md)

[pyproject.toml](../pyproject.toml) defines the installation requirements. The base
package provides the `pgw` CLI and public library API. Key operations additionally
need an available allowed system keyring backend; installing the Python package
does not configure that system service.

<!-- pgw-section: base-dependencies -->
## Direct dependencies

| Group | Requirement | Purpose |
| --- | --- | --- |
| Runtime | `cryptography>=42` | Fernet encryption and integrity checks in `crypto.py` |
| Runtime | `keyring>=25` | System key storage through `keystore.py` |
| Runtime | `PyYAML>=6` | Detector/routing configuration through `yaml.safe_load` |
| Build | `setuptools>=77.0.3`, `wheel` | wheel/sdist backend and license metadata |
| Development, `dev` | `pytest>=8`, `pytest-cov>=5` | Tests and coverage |
| Development, `dev` | `ruff>=0.6`, `mypy>=1.11`, `types-PyYAML` | Lint and static typing |
| Development, `dev` | `pre-commit>=3.7`, `detect-secrets>=1.5` | Hooks and secret scanning |

These are direct requirements. The installer resolves transitive dependencies,
which may differ by OS and Python version; `cryptography` and `keyring`, for
example, may add platform packages. Development dependencies are not base runtime
requirements. There is no lockfile pinning the full runtime graph. Lower bounds
do not promise identical resolved versions; record actual versions for a specific
environment. Exact CI covers Python 3.11/3.12 on Ubuntu/Windows. Installation
compatibility `requires-python >=3.11` does not expand that support list.

<!-- pgw-section: optional-boundary -->
## Base and extras

Base imports and `pgw --help` use the runtime requirements above without selecting
a system keyring backend or operating on keys. Subprocess tests enforce that
boundary. The only declared extra is `dev`; it adds development tools, not product
features. There are no product extras for NER, HTTP proxy, provider adapters or
model downloads, nor a public path requiring such an optional package.

Names `ner` and `proxy` are reserved in the plan for possible separately approved
features; they are not published as empty extras. Before implementation, define
capability, dependencies, lazy-import boundary and the tested missing-extra error
in a separate decision. A provider SDK also needs an explicit adapter contract;
an extra name is not that decision.

This document changes no base dependencies, public commands, exceptions or
security guarantees. See the [security guide](SECURITY.md) for keyring requirements
and the trust boundary.

## Checks

`tests/test_installation_dependencies.py` checks direct base requirements, extras
and consistency of Python support with CI. A separate process tests public import
and help while forbidding development/NER/framework/provider imports and keyring
backend selection.

These complement `tools/verify_package_build.py`, which installs wheel and sdist
in two separate clean venvs. It checks modules, `py.typed`, license/build metadata
and absence of known private/cache/environment paths, then runs installed `pgw`,
compares versions and performs a synthetic API round trip outside checkout.
Network access and system-keyring selection are forbidden in that smoke; keys
exist only in memory and key access is replaced by local test doubles. Membership
checks are not an arbitrary-secret scanner for file contents.

From a clean Git checkout:

```bash
python tools/verify_package_build.py --temp-root /path/to/temporary-directory
```

Supply an actual temporary directory outside checkout. Build/installation may
fetch dependencies from a package index; the network ban applies to the API
smoke, not to a claim of fully offline installation. CI runs this on Python
3.11/3.12 and Windows/Ubuntu; each OS/SHA needs its own primary job results.
An import check alone cannot prove all optional-library graphs or platform
keyring operation.

<!-- pgw-section: diagnostics -->
## Safe initial diagnostics

[ADR-113](ADR-113-safe-doctor.md) defines doctor; `pgw --describe` gives current
syntax. It checks installation, required imports, supported Python and capability
of the platform's standard keyring. Configuration is checked only when explicitly
selected. There is no automatic repair, extra installation, key read or report
upload.

Example for a healthy installation with an available standard backend:

```text
PACKAGE: OK
PYTHON: OK
DEPENDENCIES: OK
KEYRING: OK
CONFIG: NOT_REQUESTED
OPTIONAL: NOT_AVAILABLE
SUMMARY: OK
```

Only these check names and closed statuses are printed, not user paths, backend
names, credentials or configuration contents. The report itself may be shared;
do not attach the chosen configuration file or environment. An unavailable backend
gives WARNING/exit 0; a missing required dependency or invalid selected config
gives FAILURE/exit 3. A successful predicate does not prove a vault is unlocked
or contains a key. It checks the standard backend, not custom overrides. Linux
permits only local D-Bus capability queries without activation, prompting or
unlocking; external networking is forbidden. The child check has one 5-second
wait and 1024 output bytes, no retry; failure/timeout gives WARNING.

Read-only covers diagnostic checks, user data, configuration and keyring. Ordinary
`pgw doctor` may create standard Python `__pycache__` during imports. To exclude
those writes, use `python -B -m privacy_gateway doctor` with the selected
environment's interpreter. This does not promise zero writes by the process or
OS; see the exact boundary in ADR-113.
