# Contributing to GMOS

This document describes how to safely and productively contribute to GMOS’ codebase.

GMOS is a GPL-3.0 open-source project. All contributions must comply with:
- the license
- the security model
- project formatting and testing rules
- the deterministic behavior requirements of the patching engine

---

# 1. Development Environment Setup

## Requirements
- Python 3.10–3.12
- Git
- A modern code editor (VSCode, PyCharm, etc.)

## Install dependencies

```sh
# Create a virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dev tools
pip install -r requirements-dev.txt
pip install -e .
```

-----

# 2. Code Quality & Formatting

GMOS enforces strict style rules. Before committing, please run the following tools manually to ensure CI passes:

### Formatting (Black)

```sh
black .
```

### Linting (Ruff & Flake8)

```sh
ruff check .
flake8 .
```

### Type Checking (MyPy)

```sh
mypy .
```

All commits must pass these checks.

-----

# 3. Branching Strategy

| Branch       | Purpose                       |
| ------------ | ----------------------------- |
| `main`       | Stable public release         |
| `dev`        | Active development            |
| `feature/*`  | Single new feature            |
| `fix/*`      | Patches and bugfixes          |
| `security/*` | Security-related improvements |

PRs should be made against `dev`. Merges to `main` occur only after full CI validation.

-----

# 4. Contribution Requirements

Every PR must include:

  * clear description of behavior change
  * test coverage for new code paths
  * no unverified security-impacting changes
  * SPDX license header for every new file

-----

# 5. Review Process

1.  **Automated CI runs:**

      * Tests across Python versions (3.10 - 3.12)
      * Static analysis (Lint/Type)
      * Security scan (Bandit/Safety)

2.  **Human review enforces:**

      * Deterministic behavior
      * Consistency with GMOS architecture
      * No sandbox bypasses

3.  **Approval** → Squash merge to `dev`.
