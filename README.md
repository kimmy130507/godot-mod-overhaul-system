# **GMOS — Godot Mod Overhaul System**

**A safe, deterministic, and flexible mod loader for Godot Engine games.**

GMOS generates a patched working copy of a game at launch time, allowing mods to replace assets, patch scripts, and inject logic **without permanently altering the original installation**.

It features a **"Gold Standard" Architecture** built on six pillars (Core, IO, State, Net, UI, Utils) to ensure robustness, thread safety, and testability.

## **Flagship Features**

* **Merge Studio:** A built-in visual conflict resolver. When mods collide, view side-by-side diffs and write custom patches directly in the UI.
* **Nexus Mods Integration:** Full download manager supporting one-click `nxm://` links, bandwidth tracking, and automatic metadata resolution.
* **Profiles & Data Isolation:** Create distinct mod lists (e.g., "Vanilla+" vs "Overhaul") and optionally isolate your save files so modded playthroughs don't corrupt vanilla saves.
* **P2P Multiplayer Sync:** Host a local lobby to automatically sync your exact mod load order and archives to connecting friends.
* **Active Security (Sandboxing):** Actively rewrites unsafe GDScript (like `OS.execute` or `load("malware.dll")`) at runtime to prevent malicious code execution.
* **Override PCK Architecture:** Compiles modded assets into a standalone `gmos_override.pck` mounted dynamically at runtime—zero disk bloat, zero original file corruption.
* **Developer SDK:** Built-in tooling to decompile games (via GDRE Tools), auto-detect code diffs, and generate `mod.mos` manifests automatically.

## **Quick Start (Players)**

1. Download the **GMOS Executable** (`gmos.exe` or `gmos`) from **Releases**.
2. Launch GMOS.
3. Use the **Instance Manager** to add and activate your game directory.
4. Download mods manually or via the integrated **Browser**.
5. Click **Apply Patch** → **Run**.

See the [User Guide](docs/UserGuide/) for detailed instructions.

## **Quick Start (Mod Authors)**

GMOS mods use a simple folder structure with a `mod.mos` manifest.

```ini
[ModInfo]
name = My Mod
version = 1.0.0

[FileReplace]
res://scripts/player.gd = patches/player.gd

[SmartPatch]
res://scripts/combat.gd::take_damage = patches/damage_hook.gd ; anchor="health -= amt"

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

1. **Static Analyzer (Scanner):** Audits files for suspicious patterns (`FileAccess`, `DirAccess`) and warns the user before installation.
2. **Runtime Sanitizer (Rewriter):** Actively modifies script code during the patch run to redirect RCE vectors (`OS.execute`, `OS.shell_open`) to a strict sandbox proxy (`GMOS_Sandbox`).

See [Security Overview](docs/Security/Overview.md).

## **Documentation**

Comprehensive documentation is available in the repository:

* **[UserGuide](docs/UserGuide/)** — Installation, Profiles, P2P Sync, Troubleshooting
* **[ModAuthorGuide](docs/ModAuthorGuide/)** — Manifests, SDK, Patch Types, Merge Studio
* **[Internals](docs/Internals/)** — Deep dives (Locking, Override PCKs, Data Schemas)
* **[Security](docs/Security/)** — Threat Model, Sanitization
* **[Development](docs/Development/)** — Architecture, CI/CD, Contributing

**Start here:** ➡ [Architecture Overview](docs/Development/Architecture.md)

## **Contributing**

Pull requests are welcome! Please ensure you follow the **6-Pillar Architecture** and run the test suite.

* **Linting:** `ruff`, `black`, `mypy` (Strict)
* **Tests:** `pytest` (Core, IO, Net, State, UI, Utils)

See [Contributing Guide](docs/Development/Contributing.md).

## **Legal Notice**

GMOS does **not** circumvent DRM and cannot run commercial games without their standard executable. You are responsible for reviewing and complying with the **EULA** of any game you modify. GMOS is provided "AS IS" and authors are not liable for account restrictions.

## **License**

GMOS is licensed under **GPL-3.0-or-later**.