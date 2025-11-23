# Build & Release Process

This document describes how to create official GMOS releases.

# 1. Versioning

GMOS uses semantic versioning:
`MAJOR.MINOR.PATCH` (e.g., `1.0.0`).

The single source of truth for the version is **`pyproject.toml`**.


# 2. Release Steps

### 1. Merge `dev` → `main`
Ensure all CI checks pass before merging.

### 2. Update Version
Edit `pyproject.toml`:
```toml
[project]
version = "1.2.0"
```

### 3. Generate Release Notes

Include:

  - New features
  - Bug fixes
  - Security patches
  - Breaking changes

### 4. Tag the Release

Git tags trigger the packaging workflow.

```sh
git tag v1.2.0
git push --tags
```

### 5. Publish GitHub Release

The CI pipeline will automatically attach the following artifacts:

  * **GMOS Executable** (Windows `.exe`, Linux/macOS binary)
  * **Source Tarball/Wheel** 
  * **SHA256 Checksums**
  * **GPG Signatures** (`.sig.asc`)

# 3. PyPI Release (Future)

PyPI distribution is currently planned for future releases.

```sh
python -m build
twine upload dist/*
```

# 4. Reproducibility

All builds are generated using pinned dependencies in `requirements-dev.txt` and `pyproject.toml` to ensure deterministic output.
