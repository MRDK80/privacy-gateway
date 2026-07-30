# Privacy Gateway

A local Python tool for safe preparation of corporate texts before manual transfer to an external language model.

> **Status:** Early MVP — Stage E1 (skeleton only). The `prepare` and `restore` commands are not yet implemented.

## How it works

All processing is **local**. Privacy Gateway detects sensitive entities in a text, replaces them with neutral tokens (e.g. `[PERSON_001]`), and builds an encrypted local case map. The model receives only the sanitised text — **the case map is never sent to the model**. After you copy the model response back, `restore` substitutes the original values.

In Stage E1 none of the above is active. Only the project skeleton, configuration examples, and documentation are present.

## Requirements

- Windows (primary target for MVP); Linux supported for CI
- Python 3.11+

## Installation

```powershell
# Create and activate a virtual environment
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

If PowerShell execution policy blocks activation:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Usage

```powershell
pgw --help
python -m privacy_gateway --help
```

In Stage E1, calling `pgw prepare` or `pgw restore` exits with a non-zero code and the message:

```
Command is not available in stage E1.
```

## Development checks

```powershell
pytest
ruff check .
mypy src
detect-secrets scan --all-files --exclude-files "\.git/.*"
```

## ⚠️ Security policy

**Do not add real corporate data to this repository.**
This includes names, organisations, email addresses, server names, IP addresses, document references, financial figures, or any other identifying information.

See [SECURITY.md](SECURITY.md) for the full policy.

Privacy Gateway does **not** guarantee legal, absolute, or complete data protection. It is a development-stage tool and must be used as part of a broader security practice.

## Roadmap

| Stage | Description |
|-------|-------------|
| **E1** | Project skeleton, config examples, documentation |
| E2 | `prepare`: entity detection, tokenisation, encrypted case map |
| E3 | `restore`: token integrity check, back-substitution |
| E4 | Secret scanning integration, routing recommendations |
