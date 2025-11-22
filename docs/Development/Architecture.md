# GMOS Architecture Overview

The GMOS (Godot Mod Overhaul System) architecture is built upon **Five Pillars**, designed to separate concerns between business logic, user interface, data persistence, and low-level file operations.


This modular design allows for:
- **Headless Operation:** The Core and IO layers function independently of the UI.
- **Atomic Safety:** All file operations are routed through a dedicated IO layer.
- **Strict Typing:** The entire codebase adheres to strict MyPy standards.

---

# 1. The Five Pillars

## 1.1. Core (`gmos.core`)
*The Brain.* Contains the business logic, orchestration, and security enforcement mechanisms.

* **Session (`session.py`)**: The central orchestrator. It manages the application state, mod loading, and coordinates the patching process. It acts as the API that the UI consumes.
* **Patcher (`patcher.py`)**: Responsible for dependency resolution (topological sort), generating patch plans, and executing hunk-based merges. It implements the logic for `FileReplace`, `VariablePatch`, and `FunctionPatch`.
* **Security (`security.py`)**: The **Static Analysis** engine. Contains the custom GDScript Lexer/Tokenizer used to scan for malicious patterns (ACE vectors) before execution.
* **Injection (`injection.py`)**: The **Active Defense** mechanism. Handles the injection of the `GMOS_Sandbox` autoload into `project.godot` and manages the runtime security payload.
* **SDK (`sdk.py`)**: The **GodotBridge**. Wraps external tools (GDRE Tools) to provide decompilation capabilities and workspace management for mod authors.

## 1.2. IO (`gmos.io`)
*The Muscle.* Handles all file system interactions, ensuring atomicity and concurrency safety.

* **Base (`base.py`)**: Implements atomic write operations (`atomic_write_with_backup`, `atomic_replace`). It uses a "write-temp-then-rename" strategy to prevent data corruption.
* **Locking (`locking.py`)**: Manages cross-process locking (using Mutex on Windows, FileLock on Unix) to prevent multiple GMOS instances from modifying the same game directory simultaneously.
* **PCK (`pck.py`)**: A binary parser/writer for Godot `.pck` archives, allowing for non-destructive appending of modded assets.
* **Cache (`cache.py`)**: Manages the cleanup of Godot's `.import` folder to resolve stale asset issues.

## 1.3. State (`gmos.state`)
*The Memory.* Manages configuration, persistence, and user policies.

* **Config (`config.py`)**: Handles loading and saving of the global application configuration (`config.json`), including first-run setup states.
* **Policy (`policy.py`)**: Persists user decisions regarding file conflicts (e.g., "Mod A wins over Mod B for `player.gd`"). This enables deterministic re-patching without prompting the user again.
* **Profiles (`profiles.py`)**: Manages the import/export of mod lists and load orders, allowing users to share reproducible mod setups.

## 1.4. UI (`gmos.ui`)
*The View.* A "Humble View" implementation using Tkinter (modernized with `ttkbootstrap`).

* **App (`ui.py`)**: The main window logic. It does **not** contain business logic. Instead, it delegates commands to the `GmosSession` and listens for updates via a queue-based mechanism.
* **Components**: Includes the `HunkViewer` for visual conflict resolution and `ResolveDialog` for managing load order.

## 1.5. Utils (`gmos.utils`)
*The Glue.* Shared helpers and constants.

* **Logging**: Centralized logging configuration (`gmos.log`).
* **Paths**: Cross-platform path normalization and constant definitions (`LOG_DIR`, `ROOT_DIR`).

---

# 2. Data Flow

1.  **Initialization**: `gmos.main` acquires the app lock via `gmos.io.locking` and initializes the `GmosSession`.
2.  **Loading**: `GmosSession` scans the `mods/` folder using `gmos.utils` helpers and parses manifests via `gmos.core.patcher`.
3.  **Resolution**: `gmos.core.patcher` calculates the dependency graph and identifies conflicts.
4.  **User Input**: If conflicts exist, `gmos.ui` prompts the user. Decisions are saved to `gmos.state.policy`.
5.  **Execution**:
    * `gmos.core.security` scans files.
    * `gmos.core.injection` ensures the sandbox is active.
    * `gmos.core.patcher` applies changes in memory.
    * `gmos.io` writes final files to disk atomically.

---

# 3. Key Architectural Patterns

* **Session-Based Orchestration**: The UI is decoupled from logic. This allows the potential for a CLI-only mode (`headless`) that reuses the exact same `GmosSession` logic as the GUI.
* **Atomic Writes**: All file modifications follow a strict `write temp -> flush -> rename` pattern to guarantee data integrity even during crashes.
* **Strict Typing**: The codebase adheres to `Strict` MyPy and Pylance standards to ensure robustness.