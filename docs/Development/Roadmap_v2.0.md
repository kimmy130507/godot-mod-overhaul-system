# GMOS v2.0 Roadmap: The Universal Platform

**Objective:** Transition GMOS from a single-game utility into a professional-grade, multi-game manager with integrated network capabilities and advanced optimization.

---

# 1. Phase 1: Multi-Game Architecture (The Foundation)

**Goal:** Decouple the application from a single game directory to support managing multiple titles simultaneously.

### 1.1. Instance Manager (`gmos.core.manager`)
* **New Controller:** Introduce `InstanceManager` to govern the lifecycle of game contexts.
* **Context Switching:** Implements locking logic to safely switch the active `GmosSession` between different game paths without restarting the app.
* **Discovery:** Auto-scan heuristics to find installed Godot games (Steam/Itch).

### 1.2. Two-Tiered Configuration
* **Global Registry (`global_config.json`):** Stores app-wide settings (theme, default instance) in `%APPDATA%`.
* **Instance Config (`instance.json`):** Stores game-specific settings (mods path, executable) inside the game's data folder.
* **Migration:** Auto-convert legacy v1.0 `config.json` to the new format on first launch.

### 1.3. UI Overhaul
* **Selector View:** A new landing page to select or add game instances.
* **Dashboard View:** The existing mod management interface, loaded only after an instance is selected.

---

# 2. Phase 2: The Network Pillar (The Bridge)

**Goal:** Remove the friction of manual downloads by integrating mod repositories directly into the client.

### 2.1. `gmos.net` Module
* **Repository Interface:** Abstract provider class for connecting to APIs (Nexus, Thunderstore, GitHub).
* **Download Manager:** Async, stream-based downloader utilizing the `gmos.io` thread pool.
* **Mod Browser:** New UI tab to search, filter, and install mods from within the app.

### 2.2. Trust Chain Security
* **Quarantine:** Downloads are staged in a temporary folder.
* **Auto-Scan:** The `Static Analyzer` immediately scans new downloads.
* **Consent Gate:** If "High Severity" risks (RCE/Binary) are found, the user must explicitly override a warning dialog to install.

---

# 3. Phase 3: Core Optimization (The Performance)

**Goal:** Eliminate bottlenecks in the patcher and harden security against obfuscation.

### 3.1. Parallel Patcher
* **DAG Scheduling:** Group patch operations by target file.
* **Concurrency:** Execute patches for *independent* files in parallel using the thread pool.
* **Safety:** Operations targeting the *same* file remain serialized to preserve deterministic load order.

### 3.2. PCK Optimization
* **PCK Context:** Implement a context manager that parses the PCK header **once** per run.
* **O(1) Seek:** Replace repeated linear scans with direct offset lookups, reducing patch time for large archives significantly.

### 3.3. AST Injection (Active Defense v2)
* **Token Stream Rewriting:** Move from `re.sub` (Regex) to `GDScriptLexer` (Token Stream) for sanitization.
* **Obfuscation Resistance:** Detects and neutralizes malicious calls even if hidden by string concatenation or variable aliasing.
* **Binary Project Support:** Fallback injection via `override.cfg` for games that use `project.binary`.