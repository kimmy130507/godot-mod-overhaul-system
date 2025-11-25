# Locking & Concurrency Model

This document explains how GMOS guarantees data integrity through a two-layered locking strategy. These mechanisms operate at completely different scopes to solve two different concurrency problems: **Process Conflict** and **Thread Conflict**.

They do not interact directly but work in tandem to provide full safety.

# 1. The Two Layers of Defense

| Layer | Scope | Guard Against | Mechanism | Source |
| :--- | :--- | :--- | :--- | :--- |
| **Outer** | OS / Filesystem | Multiple GMOS instances | File Lock (`.gmos.lock`) | `gmos.io.locking` |
| **Inner** | Process / RAM | Background Threads | Race conditions | `gmos.io.base` |

# 2. Outer Defense: Process Safety

**Goal:** Prevent "Instance Conflict" (e.g., User opens GMOS twice and Instance 1 fights Instance 2 over the same files).

### Implementation
* **Location:** `gmos.io.locking` (called via `utils` or `main`).
* **Target:** Starts at `logs/gmos.lock` (Global), then switches to `<GameDir>/.gmos.lock`.
* **Technique:** OS-level file locking via `msvcrt` (Windows) or `fcntl` (Linux/macOS).

### Behavior
1.  **Startup**: GMOS acquires the global `logs/gmos.lock` to initialize safely.
2.  **Handover**: Once a game is selected, it acquires `.gmos.lock` in the **Game Directory** and releases the global lock.
3.  **Enforcement**: If another instance holds the lock for *that specific game*, access is denied.

This allows you to open multiple GMOS instances simultaneously, provided they are managing **different games**.

This effectively enforces a **Singleton Pattern** at the operating system level.

### Reliability Features
To handle real-world edge cases (like slow OS cleanup or user error), the Outer Defense includes:

* **Smart Grace Period (Livelock Prevention):**
    When restarting the app quickly, the OS may still report the file as "locked" by the previous closing process. Instead of crashing immediately, GMOS detects if the holding process is running and waits **0.5 seconds** for it to exit. This transparently fixes "False Alarm" locking errors.

* **Lock Rejection (Force Revert):**
    If a user tries to switch the UI to a game that is *already open* in another window, the Lock Manager rejects the acquisition. Crucially, the UI is then **forced to revert** to its previous valid selection. This prevents a "Limbo State" where the UI displays Game B but the backend is still locked to Game A.

* **Startup Sanity Check:**
    GMOS refuses to acquire a lock or start up if the configured directory does not contain valid game files (`.pck` or `.exe`). This prevents accidental locking of system folders (like `%AppData%`) if the configuration is corrupt or default.
    
# 3. Inner Defense: Thread Safety

**Goal:** Prevent "Thread Conflict" (e.g., The UI thread saves `config.json` at the exact moment a background Patcher thread tries to read it).

### Implementation
* **Location:** `gmos.io.base`.
* **Target:** Specific file paths (e.g., `C:/Game/project.godot`).
* **Technique:** Python `threading.RLock` (Reentrant Lock) stored in a `WeakValueDictionary`.

### Behavior
1.  **Path-Based Locking:** GMOS creates a unique mutex object for every absolute file path string.
2.  **Scope:** These locks exist **only in RAM**. They are invisible to other programs.
3.  **Automatic Cleanup:** Because a `WeakValueDictionary` is used, locks for files that aren't currently being accessed are automatically garbage collected to save memory.

### Usage
Any internal function that writes to a file (e.g., `atomic_write_with_backup`) first acquires the specific thread lock for that path. This serializes write operations *within* the single running process.

# 4. Summary: How They Work in Tandem

1.  **Outer Defense (`gmos.lock`)** guarantees that **only one** GMOS process is touching the game folder.
2.  **Inner Defense (`io.base`)** guarantees that the **many threads** inside that single process do not corrupt files by writing to them simultaneously.

Together, they ensure ACID-like properties for file operations: either a file is written completely and safely, or it isn't written at all.