# **GMOS — Godot Mod Overhaul System**

**A safe, deterministic, and flexible mod loader for Godot Engine games**

GMOS generates a patched working copy of a game at launch time, allowing mods to replace assets, patch scripts, and inject logic **without permanently altering the original installation**.

It features a **"Gold Standard" Architecture** built on five pillars (Core, IO, State, UI, Utils) to ensure robustness, thread safety, and testability.

## **Features**

* **Atomic Patching:** Never corrupts game files; uses a "Write-Replace" strategy with full rollback.
* **Deterministic Engine:** Resolves conflicts, dependencies, and load order using stable sorting algorithms.
* **Active Security:** Rewrites unsafe GDScript (`OS.execute`) at runtime to prevent malicious code execution.
* **Native PCK Support:** Appends modded assets directly to `.pck` archives using a pure-Python parser (no external tools required for patching).
* **Static Analysis:** Scans mods for suspicious file access or binary loading before installation.
* **Developer SDK:** Built-in tooling to decompile games (via GDRE Tools) and auto-generate manifests from workspace diffs.

## **Quick Start (Players)**

1. Download the **GMOS Executable** (`gmos.exe` or `gmos`) from **Releases**.
2. Launch GMOS.
3. Follow the **Setup Wizard** to select your **Game Executable**.
4. Click **Refresh Mods** → Enable the mods you want.
5. Click **Apply Patch** → **Start Game**.

See the [User Guide](docs/UserGuide/) for detailed instructions.

## **Quick Start (Mod Authors)**

GMOS mods use a simple folder structure with a `mod.mos` manifest.

```ini
[ModInfo]
name = My Mod
version = 1.0.0

[FileReplace]
res://scripts/player.gd = patches/player.gd
```

Full documentation is available in the [Mod Author Guide](docs/ModAuthorGuide/).

## **Developer SDK (GodotBridge)**

GMOS includes a Python SDK (`gmos.core.sdk`) to automate the modding workflow.

  * **Decompile:** Converts binary assets back to editable source.
  * **Diff:** Auto-detects changes in your workspace.
  * **Package:** Generates `mod.mos` and zip files automatically.

*Note: The SDK requires **GDRE Tools** to perform the initial decompilation step.*

See [Workspace SDK](docs/ModAuthorGuide/WorkspaceSDK.md).

## **Security Architecture**

GMOS employs a **Two-Layer Defense** model:

1.  **Static Analyzer (Scanner):** checks files for suspicious patterns (`FileAccess`, `DirAccess`) and warns the user.
2.  **Runtime Sanitizer (Rewriter):** actively modifies script code to redirect RCE vectors (`OS.execute`) to a safe sandbox proxy (`GMOS_Sandbox`).

See [Security Overview](docs/Security/Overview.md).

# **Native PCK Support**

GMOS handles Godot `.pck` files natively. It does **not** require external repackers.

  * **Safe Append:** Modded files are appended to the end of the PCK, and the header index is updated.
  * **Atomic:** If the write fails, the original PCK is restored instantly.

See [PCK Rebuild Internals](docs/Internals/PCKRebuild.md).

# **Documentation**

Comprehensive documentation is available in the repository:

* [**UserGuide**](docs/UserGuide/) — Installation, Troubleshooting
* [**ModAuthorGuide**](docs/ModAuthorGuide/) — Manifests, SDK, Patch Types
* [**Internals**](docs/Internals/) — Deep dives (Locking, Data Schemas)
* [**Security**](docs/Security/) — Threat Model, Sanitization
* [**Development**](docs/Development/) — Architecture, CI/CD, Contributing

**Start here:**
➡ [Architecture Overview](docs/Development/Architecture.md)

## **Contributing**

Pull requests are welcome!
Please ensure you follow the **5-Pillar Architecture** and run the test suite.

  * **Linting:** `ruff`, `black`, `mypy` (Strict)
  * **Tests:** `pytest` (Core, IO, UI)

See [Contributing Guide](docs/Development/Contributing.md).

## **Legal Notice**

GMOS does **not** circumvent DRM and cannot run commercial games without their standard executable.
You are responsible for reviewing and complying with the **EULA** of any game you modify.

## **License**

GMOS is licensed under **GPL-3.0-or-later**.