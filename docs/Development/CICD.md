# CI/CD Pipeline Documentation

GMOS uses GitHub Actions for automated validation, testing, and packaging.

# 1. CI Stages

The pipeline (`workflows/ci.yml`) runs on every push and PR to `main` or `dev`.

1. **Setup**: Python 3.10, 3.11, 3.12 on Ubuntu & Windows.
2. **Compliance**: Checks for license headers in source files.
3. **Static Analysis**:
   - `black` (formatting)
   - `ruff` & `flake8` (linting)
   - `mypy` (type checking)
4. **Security Scan**:
   - `bandit` (AST analysis for python vulnerabilities)
   - `safety` (checks dependencies against CVE database)
5. **Tests**:
   - `pytest` with coverage reporting

# 2. Packaging

The packaging workflow (`workflows/package.yml`) runs on version tags (`v*`) and via manual dispatch.

### Artifacts Produced
1. **GMOS Application Binary** (`GMOS` / `GMOS.exe`)
   - A single-file executable built via **PyInstaller**.
   - Bundled as a standalone executable (Windows/Linux) or DMG (macOS).
2. **Python Distributions**:
   - **Source Tarball** (`.tar.gz`) for distribution.
   - **Wheel** (`.whl`) for pip installation.
3. **Checksums**: `SHA256` hashes for verification.
4. **GPG Signatures**: Detached signatures (`.sig.asc`) for provenance (Windows builds).

# 3. Build Environment

- **Linux**: Ubuntu-latest, uses `inkscape` & `imagemagick` for icon generation.
- **Windows**: Windows-latest, uses `magick`.
- **macOS**: macOS-latest, uses `iconutil` and `create-dmg`.

All builds rely on `pyproject.toml` and `gmos.spec` for configuration.

# 4. E2E Testing

A separate workflow (`workflows/e2e-headless.yml`) performs headless UI simulation tests.

- **Trigger**: Pushes to `main` or `release/*`.
- **Environment**: Runs on Linux (via `xvfb`), Windows, and macOS.
- **Scope**: Validates patcher operations in a simulated GUI environment.