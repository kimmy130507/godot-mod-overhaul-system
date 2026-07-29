# GMOS Architecture Overview

The GMOS (Godot Mod Overhaul System) architecture is built upon **Six Pillars**, designed to separate concerns between business logic, user interface, data persistence, networking, and low-level file operations.

This modular design allows for:
- **Headless Operation:** The Core and IO layers function independently of the UI.
- **Atomic Safety:** All file operations are routed through a dedicated IO layer.
- **Strict Typing:** The entire codebase adheres to strict MyPy standards.

### Root Entrypoints
* **Main (`gmos/main.py`)**: The primary launcher handling singleton application locking, protocol link registration (`nxm://`), headless dry-run generation, and GUI initialization.
* **CLI (`gmos/cli.py`)**: The dedicated Command Line Interface for managing mods, applying patches, and establishing P2P connections independently of the graphical interface.

# 1. The Six Pillars

## 1.1. Core (`gmos.core`)
*The Brain.* Contains the business logic, orchestration, and security enforcement mechanisms.

* **Project (`godot_project.py`)**: Parsers for reading and modifying `project.godot` configuration files.
* **Injection (`injection.py`)**: The **Active Defense** mechanism. Handles the injection of the `GMOS_Sandbox` autoload into `project.godot` and manages the runtime security payload. See [Injection System Internals](docs/Internals/InjectionSystem.md).
* **Parser (`parser.py`)**: Contains `GDScriptLexer` and `CSTParser` to extract structural semantics, tokenize source code, and resync on unknown statements.
* **Patcher (`patcher.py`)**: Responsible for dependency resolution (topological sort), generating patch plans, and executing merges via DAG-based Parallel Scheduling. It utilizes the `CSTParser` for structural analysis and contains the logic for patch types. See [Patch Pipeline Internals](docs/Internals/PatchPipeline.md).
* **Protocol (`protocol.py`)**: Handles OS integration (Registry) for `nxm://` links and local IPC to ensure single-instance handling of external commands.
* **SDK (`sdk.py`)**: The **GodotBridge**. Wraps external tools (GDRE Tools) to provide decompilation capabilities and workspace management for mod authors.
* **Security (`security.py`)**: The **Static Analysis** engine. Contains the custom GDScript Lexer/Tokenizer used to scan for malicious patterns (ACE vectors) before execution. See [Threat Model](docs/Security/ThreatModel.md).
* **Session (`session.py`)**: The central orchestrator. It manages the application state, mod loading, and coordinates the patching process. It acts as the API that the UI consumes.
* **Tools (`tools.py`)**: Manages external binaries and environment configuration.

## 1.2. IO (`gmos.io`)
*The Muscle.* Handles all file system interactions, ensuring atomicity and concurrency safety.

* **Base (`base.py`)**: Implements atomic write operations (`atomic_write_with_backup`, `atomic_replace`). It uses a "write-temp-then-rename" strategy to prevent data corruption.
* **Cache (`cache.py`)**: Manages the cleanup of Godot's `.import` folder to resolve stale asset issues.
* **Locking (`locking.py`)**: Manages cross-process locking (using `msvcrt` byte-locking on Windows, `fcntl` on Unix) to prevent multiple GMOS instances from modifying the same game directory simultaneously.
* **PCK (`pck.py`)**: A binary parser/writer for Godot `.pck` archives, used for packing modified assets into `gmos_override.pck`.

## 1.3. State (`gmos.state`)
*The Memory.* Manages configuration, persistence, and user policies.

* **Config (`config.py`)**: Handles loading and saving of the global registry (`global_registry.db`) and instance configuration (`instance.json`), including first-run setup states.
* **Policy (`policy.py`)**: Persists user decisions regarding file conflicts to `user_load_order.json` (e.g., "Mod A wins over Mod B for `player.gd`"). This enables deterministic re-patching without prompting the user again.
* **Profiles (`profiles.py`)**: Manages the import/export of mod lists and load orders, allowing users to share reproducible mod setups.

## 1.4. Net (`gmos.net`)
*The Communicator.* Handles network operations, API integrations, and asset retrieval. See [Network Stack Internals](docs/Internals/NetworkStack.md).

* **API (`api.py`)**: Defines abstract interfaces for mod repository interaction.
* **Downloader (`downloader.py`)**: Manages asynchronous downloads with progress tracking and pause/resume support.
* **Manifest (`manifest.py`)**: Handles parsing of remote update manifests.
* **P2P (`p2p.py`)**: Manages peer-to-peer networking functionality.
* **Providers (`providers.py`)**: Implementations for specific services (e.g., Nexus, Thunderstore).

## 1.5. UI (`gmos.ui`)
*The View.* A "Humble View" implementation using Tkinter (modernized with `ttkbootstrap`).

* **App (`app.py`)**: The main window logic. It does **not** contain business logic. Instead, it delegates commands to the `GmosSession` and listens for updates via a queue-based mechanism.
* **Browser (`browser.py`)**: The **Download Manager** view. Handles the visualization of active downloads and bandwidth usage. (Legacy name).
* **Dashboard (`dashboard.py`)**: The main landing view displaying the mod list and filters.
* **Instances (`instances.py`)**: Manages game instances and selection logic.
* **Logs (`logs.py`)**: Displays application logs and the dry-run console.
* **Dev (`dev.py`)**: Developer Tools UI for managing mod projects and SDK operations.
* **Merge Studio (`merger.py`)**: The visual tool for conflict resolution and unified patch generation.
* **Profiles (`profiles.py`)**: UI for managing mod profiles (import/export).
* **Settings (`settings.py`)**: Global application configuration dialog.
* **Widgets (`widgets.py`)**: Shared UI components (Tooltips, Mod Info Pane, Toasts).

## 1.6. Utils (`gmos.utils`)
*The Glue.* Shared helpers and constants.

* **Logging**: Centralized logging configuration (`gmos.log`).
* **Paths**: Cross-platform path normalization and constant definitions (`LOG_DIR`, `ROOT_DIR`).

# 2. System Workflows

## 2.1. Patching Lifecycle (Core)
1.  **Initialization**: Entrypoints (`gmos.main` or `gmos.cli`) acquire the app lock (if applicable) and initialize the `GmosSession`.
2.  **Loading**: `GmosSession` scans the `mods/` folder and parses manifests.
3.  **Resolution**: `gmos.core.patcher` calculates the dependency graph and identifies conflicts.
4.  **User Input**: If conflicts exist, the **Merge Studio** (`gmos.ui.merger`) is invoked. Decisions are saved to `gmos.state.policy`.
5.  **Execution**:
    * `gmos.core.security` scans files for vulnerabilities.
    * `gmos.core.injection` ensures the sandbox is active.
    * `gmos.core.patcher` applies changes in memory (hybrid text/structural merge).
    * `gmos.io` writes final files to the VFS Cache and updates Symlinks.

## 2.2. Acquisition Lifecycle (Net)
1.  **Discovery**:
    * **API**: User queries `gmos.net.providers` (e.g., Nexus) for metadata.
    * **P2P**: User connects to a LAN host via `gmos.net.p2p` to fetch a `LobbyManifest`.
2.  **Protocol Handoff**: `nxm://` links trigger `gmos.core.protocol`, which passes the payload via IPC to the running instance.
3.  **Download**:
    * `gmos.net.downloader` spawns a background thread.
    * Files are streamed to `mods/_downloads/*.tmp`.
    * On completion, files are atomically moved to the final archive path.
4.  **Ingestion**: The UI triggers an extraction (install), making the mod available for the **Patching Lifecycle**.

# 3. Key Architectural Patterns

* **Session-Based Orchestration**: The UI is decoupled from logic. This allows for dedicated CLI modes (such as `gmos.cli` and headless execution in `gmos.main`) that reuse the exact same `GmosSession` logic as the GUI.
* **Atomic Writes**: All file modifications follow a strict `write temp -> flush -> rename` pattern to guarantee data integrity even during crashes.
* **Strict Typing**: The codebase adheres to `Strict` MyPy and Pylance standards to ensure robustness.