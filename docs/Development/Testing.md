# Testing Guide

GMOS uses pytest and full CI test automation.

---

# 1. Running Tests

To run the full suite:
```sh
pytest -q
```

For coverage (CI equivalent):

```sh
pytest --cov=. --cov-report=term
```

-----

# 2. Test Structure

The test suite mirrors the source code's "5-Pillar" architecture:

```
tests/
    core/       # Session, Patcher, Security, Injection
    io/         # Locking, PCK, Atomic Writes
    state/      # Config, Policy, Profiles
    ui/         # Dialogs, HunkViewer (Headless/Mocked)
    utils/      # Path helpers, Logging
    data/       # Test fixtures (sample mods, dummy PCKs)
```

-----

# 3. Required Test Types

Every PR must include:

### Unit Tests

  * pure function behavior (e.g., `utils.safe_norm`)
  * manifest parsing (`patcher.parse_mod_config`)
  * hashing logic

### Integration Tests

  * full patch run simulation (`patcher.run_patcher`)
  * dependency resolution cycles
  * workspace initialization (SDK)

### Security Tests

  * sandbox rewriting (`sanitize_script_content`)
  * forbidden API detection
  * path traversal prevention (`ensure_within`)

### Regression Tests

Anything that previously broke production **must** get a regression test case.

-----

# 4. Deterministic Output

All tests must be deterministic across:

  * OS (Windows/Linux/macOS)
  * Path separator quirks (`\` vs `/`)
  * Python version (3.10–3.12)

No randomness is allowed unless explicitly seeded for property-based testing.
