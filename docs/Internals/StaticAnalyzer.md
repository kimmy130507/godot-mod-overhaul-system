# Static Analysis Engine

This document details the architecture of the `gmos.core.security` module, which performs advanced auditing of GDScript code.

# 1. Why a Custom Lexer?

Regex is insufficient for robust security scanning because it cannot easily handle:
* Varying whitespace (`OS . execute`)
* Comments (`OS.execute # harmless comment`)
* String formatting

To solve this, GMOS implements a custom `GDScriptLexer` that tokenizes the source code.

### Token Types
The lexer breaks code into:
* `IDENTIFIER` (e.g., `OS`, `DirAccess`, `var_name`)
* `STRING` (e.g., `"cmd.exe"`)
* `DOT` (`.`)
* `LPAREN` / `RPAREN`
* `NEWLINE` / `SKIP` (Whitespace)

# 2. The Analyzer

The `SecurityAnalyzer` consumes the stream of tokens to detect dangerous semantic patterns, regardless of formatting.

### Detection Heuristics

#### A. Scope Aliasing (Taint Analysis)
* **Pattern:** Variable assignment to dangerous singletons (`OS`, `DirAccess`, `Directory`, `ClassDB`, `ProjectSettings`, `Engine`, `Expression`).
* **Reason:** Tracks variables that alias system classes to prevent execution bypasses. Treated globally across the file once flagged.

#### B. Remote Code Execution (RCE) & Dynamic Reflection
* **Pattern:** Execution via `OS.execute`, `OS.create_process`, `ClassDB.instantiate`, `ClassDB.create_instance`, `Engine.get_singleton`, or reflection (`call`, `callv`, `call_deferred`).
* **Severity:** **CRITICAL / HIGH**
* **Reason:** Allows arbitrary command execution or obfuscated engine subsystem access.

#### C. File Deletion
* **Pattern:** `IDENTIFIER(DirAccess)` $\to$ `DOT` $\to$ `IDENTIFIER(remove_absolute)`
* **Severity:** **HIGH**
* **Reason:** Malicious mods could delete system files.

#### D. Network and Data Exfiltration
* **Pattern:** `IDENTIFIER(HTTPClient | HTTPRequest)` $\to$ `DOT` $\to$ `IDENTIFIER(new)`
* **Severity:** **MEDIUM**
* **Reason:** Opens network connections, posing a potential data exfiltration risk for sensitive local files.

#### E. System Environment and Shell Access
* **Pattern:** `OS.get_environment` or `OS.shell_open`
* **Severity:** **MEDIUM**
* **Reason:** Exposes system variables or opens external links/binaries.

#### E. Binary Loading
* **Pattern:** `IDENTIFIER(load | preload)` -> `LPAREN` -> `STRING(*.dll | *.so | *.dylib)`
* **Severity:** **HIGH**
* **Reason:** Loading native binaries bypasses the GDScript sandbox entirely.

# 3. Analyzer vs. Sanitizer

GMOS uses a "Two-Pass" security model:

| Feature | **Static Analyzer** | **Runtime Sanitizer** |
| :--- | :--- | :--- |
| **Module** | `gmos.core.security` | `gmos.core.security` |
| **Method** | Token Stream Analysis | Token Stream Rewriting |
| **Action** | **Warns** the user in UI | **Rewrites** code on disk |
| **Scope** | Broad (Filesystem, Network, RCE) | Narrow (RCE, Shell, Load) |
| **Goal** | Audit & Awareness | Active Defense |

The Analyzer is the "Auditor" that flags suspicious behavior for review. The Sanitizer is the "Enforcer" that strictly neutralizes the most dangerous calls before the game runs.
